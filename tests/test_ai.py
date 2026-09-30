import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.ai_chat import AIChat, SUMMARY_INSTRUCTION
from config import DEFAULT_SETTINGS
from services.memory import MemoryStore
from services.messages import split_message


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class AITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        memory = MemoryStore(Path(self.directory.name) / "chat.db")
        self.user = SimpleNamespace(id=99)
        self.bot = SimpleNamespace(user=self.user, get_channel=lambda _: None, get_user=lambda _: None)
        with patch("cogs.ai_chat.MemoryStore", return_value=memory), patch("cogs.ai_chat.os.getenv", return_value=None):
            self.cog = AIChat(self.bot)
        self.cog.send_chunked_reply = AsyncMock()
        self.addAsyncCleanup(self.cog.cog_unload)

    def message(self, text="<@!99> hello", author_bot=False, attachments=None):
        return SimpleNamespace(author=SimpleNamespace(id=10, display_name="name", bot=author_bot), mentions=[self.user], mention_everyone=False, role_mentions=[], content=text, attachments=attachments or [], channel=SimpleNamespace(id=1, typing=Typing))

    async def test_mention_format_bot_filter_failed_and_empty_turns(self):
        self.cog.generate = AsyncMock(return_value=SimpleNamespace(text="reply"))
        self.cog.get_persona_for_channel = lambda _: ("persona", "")
        await self.cog.on_message(self.message())
        prompt = self.cog.generate.call_args.args[1][0]
        self.assertIn("hello", prompt)
        self.assertNotIn("<@!99>", prompt)
        self.assertEqual([row[2] for row in self.cog.memory.recent(1)], ["hello", "reply"])
        self.cog.generate.reset_mock()
        await self.cog.on_message(self.message(author_bot=True))
        await self.cog.on_message(self.message("<@99>"))
        self.cog.generate.assert_not_awaited()
        self.cog.generate.side_effect = RuntimeError("failed")
        with self.assertLogs("cogs.ai_chat", level="ERROR"):
            await self.cog.on_message(self.message())
        self.assertEqual(len(self.cog.memory.recent(1)), 2)

    async def test_only_non_image_attachment_never_reaches_model(self):
        self.cog.generate = AsyncMock()
        await self.cog.on_message(self.message("<@99>", attachments=[SimpleNamespace(content_type="application/pdf", size=1)]))
        self.cog.generate.assert_not_awaited()
        self.assertEqual(self.cog.memory.recent(1), [])

    async def test_same_channel_turns_remain_ordered_while_generation_is_async(self):
        active = 0
        maximum = 0
        async def generate(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return SimpleNamespace(text="reply")
        self.cog.generate = generate
        self.cog.get_persona_for_channel = lambda _: ("persona", "")
        await asyncio.gather(self.cog.on_message(self.message("<@99> first")), self.cog.on_message(self.message("<@99> second")))
        self.assertEqual(maximum, 1)
        self.assertEqual([row[2] for row in self.cog.memory.recent(1)], ["first", "reply", "second", "reply"])

    async def test_summary_failure_retains_all_rows_and_recovery_catches_up(self):
        for index in range(85):
            self.cog.memory.add(1, 10, f"turn {index}")
        self.cog.generate = AsyncMock(side_effect=RuntimeError("offline"))
        with self.assertLogs("cogs.ai_chat", level="ERROR"):
            await self.cog.compress_memory(1)
        self.assertEqual(len(self.cog.memory.recent(1, 100)), 85)
        self.cog.generate = AsyncMock(return_value=SimpleNamespace(text="summary"))
        await self.cog.compress_memory(1)
        self.assertEqual([row[2] for row in self.cog.memory.recent(1, 100)], [f"turn {index}" for index in range(60, 85)])
        self.assertEqual(self.cog.generate.await_count, 2)
        for call in self.cog.generate.call_args_list:
            self.assertEqual(call.args[0], SUMMARY_INSTRUCTION)
            self.assertFalse(call.kwargs["enable_search"])

    async def test_actual_generate_uses_async_sdk_search_switch_and_empty_guard(self):
        model_call = AsyncMock(return_value=SimpleNamespace(text="answer"))
        self.cog.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=model_call), aclose=AsyncMock()), close=lambda: None)
        settings = DEFAULT_SETTINGS.copy()
        with patch("cogs.ai_chat.load_settings", return_value=settings):
            await self.cog.generate("persona", "message")
            self.assertEqual(model_call.call_args.kwargs["model"], "gemini-3.8-flash")
            self.assertIsNone(model_call.call_args.kwargs["config"].tools)
            self.assertEqual(model_call.call_args.kwargs["config"].thinking_config.thinking_level.value, "LOW")
            settings["ai_search_enabled"] = True
            await self.cog.generate("persona", "message")
            self.assertEqual(len(model_call.call_args.kwargs["config"].tools), 1)
            model_call.return_value = SimpleNamespace(text=None)
            with self.assertRaises(ValueError):
                await self.cog.generate("persona", "message")


class MessageTests(unittest.TestCase):
    def test_long_emoji_and_whitespace_never_overflow_or_hang(self):
        text = "😀" * 3000
        chunks = split_message(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 1900 for chunk in chunks))
        self.assertEqual(split_message(" " * 4000), ["這次沒有取得可顯示的內容，請稍後再試。"])
        chunks = split_message("\n" + "a" * 5000)
        self.assertEqual("".join(chunks), "a" * 5000)
