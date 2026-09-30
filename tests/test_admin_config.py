import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

import config
from cogs.admin import (Admin, AdminPanel, BudgetModal, ConfirmView, LabModal,
                        ModelView, RoleView, SettingsModal, TemplateView, TextModal)
from services.memory import MemoryStore
from cogs.ai_chat import AIChat


class ConfigTests(unittest.TestCase):
    def test_invalid_intervals_models_boolean_and_ids(self):
        for change in ({"youtube_check_minutes": float("nan")}, {"youtube_check_minutes": 0}, {"youtube_check_minutes": float("inf")}, {"discord_channel_id": True}, {"youtube_channel_id": "../bad"}, {"gemini_model": "unknown"}, {"ai_search_enabled": "false"}):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                config.validate_settings(change)

    def test_save_preserves_new_settings_and_does_not_replace_on_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("config.SETTINGS_PATH", path):
                config.save_settings({"ai_search_enabled": True, "gemini_model": "gemini-3.7-flash"})
                config.save_settings({"youtube_check_minutes": 10})
                saved = config.load_settings()
                self.assertTrue(saved["ai_search_enabled"])
                self.assertEqual(saved["gemini_model"], "gemini-3.7-flash")
                original = path.read_bytes()
                with self.assertRaises(ValueError):
                    config.save_settings({"youtube_check_minutes": -1})
                self.assertEqual(path.read_bytes(), original)

    def test_corrupt_persona_store_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "personas.json"
            path.write_text("broken", encoding="utf-8")
            with patch("config.PERSONAS_PATH", path), self.assertRaises(ValueError):
                config.load_persona_store()
            self.assertEqual(path.read_text(), "broken")

    def test_corrupt_settings_cannot_send_to_default_destination_or_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("broken", encoding="utf-8")
            with patch("config.SETTINGS_PATH", path), self.assertLogs("config", level="ERROR"):
                with self.assertRaises(ValueError):
                    config.load_settings()
                with self.assertRaises(ValueError):
                    config.save_settings({"ai_search_enabled": True})
            self.assertEqual(path.read_text(), "broken")


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store_path = Path(self.directory.name) / "personas.json"
        self.store_path.write_text(json.dumps({"templates": {}, "channel_personas": {}}), encoding="utf-8")
        self.persona_patch = patch("config.PERSONAS_PATH", self.store_path)
        self.persona_patch.start()
        self.addCleanup(self.persona_patch.stop)
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        await self.bot._async_setup_hook()
        self.addAsyncCleanup(self.bot.close)
        self.bot.is_owner = AsyncMock(return_value=True)
        with patch("cogs.ai_chat.MemoryStore", return_value=MemoryStore(Path(self.directory.name) / "chat.db")), patch("cogs.ai_chat.os.getenv", return_value=None):
            await self.bot.add_cog(AIChat(self.bot))
        await self.bot.add_cog(Admin(self.bot))
        self.panel = AdminPanel(self.bot, 1, 2)

    def interaction(self, user_id=1):
        return SimpleNamespace(user=SimpleNamespace(id=user_id), response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()), edit_original_response=AsyncMock())

    async def test_views_modals_and_commands_serialize_for_discord(self):
        for view in (self.panel, TemplateView(self.panel), RoleView(self.panel), ModelView(self.panel), ConfirmView(self.panel, "memory")):
            self.assertLessEqual(len(view.to_components()), 5)
        for modal in (SettingsModal(self.panel), BudgetModal(self.panel), TextModal(self.panel, "default", "x"), TextModal(self.panel, "template"), TextModal(self.panel, "summary", "x"), LabModal(self.panel)):
            self.assertLessEqual(len(modal.to_dict()["components"]), 5)
        self.assertEqual({cmd.name for cmd in self.bot.tree.get_commands()}, {"管理", "人格匯入", "人格匯出", "help", "說明", "memory", "記憶", "forget", "忘記"})
        self.assertLess(len(self.panel.embed()), 6000)

    async def test_owner_is_checked_on_views_and_modal_not_only_command(self):
        interaction = self.interaction(9)
        self.assertFalse(await self.panel.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        self.assertFalse(await SettingsModal(self.panel).interaction_check(self.interaction(9)))
        self.bot.is_owner.return_value = False
        self.assertFalse(await self.panel.interaction_check(self.interaction(1)))

    async def test_cancelled_clear_preserves_memory_confirm_captures_target(self):
        ai = self.bot.get_cog("AIChat")
        ai.memory.add(2, 10, "keep")
        ai.memory.add(3, 10, "other")
        view = ConfirmView(self.panel, "memory")
        await view.cancel.callback(self.interaction())
        self.assertEqual(ai.memory.recent(2)[0][2], "keep")
        view = ConfirmView(self.panel, "memory")
        self.panel.channel_id = 3
        await view.confirm.callback(self.interaction())
        self.assertEqual(ai.memory.recent(2), [])
        self.assertEqual(ai.memory.recent(3)[0][2], "other")

    async def test_template_pagination_and_assignment(self):
        config.save_persona_store({"templates": {f"p{i:02}": {"name": f"Persona {i}", "content": "prompt"} for i in range(30)}, "channel_personas": {}})
        view = TemplateView(self.panel)
        await view.next_page.callback(self.interaction())
        selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
        self.assertEqual(len(selects[0].options), 5)
        view.template_id = "p29"
        self.panel.channel_id = 3
        await view.assign.callback(self.interaction())
        self.assertEqual(config.load_persona_store()["channel_personas"], {"2": "p29"})
