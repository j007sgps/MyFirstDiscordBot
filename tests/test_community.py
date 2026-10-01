import asyncio
import concurrent.futures
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import cogs.community as module
import cogs.ai_chat as ai_module
from cogs.community import ActivityView, Community, board_embed
from services.community_store import CommunityStore
from services.time_input import parse_time, poll_options


class TimeTests(unittest.TestCase):
    def test_duration_calendar_timezone_limits_and_options(self):
        now = datetime(2026, 10, 1, 3, tzinfo=timezone.utc)
        self.assertEqual(parse_time("1小時30分", now=now), now.timestamp() + 5400)
        self.assertEqual(parse_time("明天 21:00", now=now), datetime(2026, 10, 2, 12, tzinfo=timezone.utc).timestamp())
        self.assertEqual(parse_time("2026-10-01 14:00+08:00", now=now), now.timestamp() + 10800)
        for text in ("59秒", "0分鐘", "31天", "今天 11:00", "明天 25:00", "30分鐘垃圾", "10" * 100 + "天"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_time(text, now=now)
        self.assertEqual(poll_options("拉麵，咖哩、遊戲"), ["拉麵", "咖哩", "遊戲"])
        for text in ("一個", "a,a", "a,,b", "a," + "b" * 61, ",".join(str(n) for n in range(11))):
            with self.assertRaises(ValueError):
                poll_options(text)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.db"
        self.store = CommunityStore(self.path)
        self.now = 1000

    def tearDown(self):
        self.temp.cleanup()

    def board(self, kind="poll", **kwargs):
        board_id = self.store.create_board(kind, 1, 2, 3, "test", 2000, options=["a", "b"], now=self.now, **kwargs)
        job = self.store.job_for(f"publish:{board_id}")
        self.store.part_sent(job["id"], 123)
        self.store.delivered(job["id"], now=self.now)
        return board_id

    def test_poll_change_withdraw_freezes_and_recovers_once(self):
        board_id = self.board()
        self.store.act(board_id, 4, "vote", 0, now=1001)
        self.store.act(board_id, 4, "vote", 1, now=1002)
        self.assertEqual([row["choice"] for row in self.store.members(board_id)], [1])
        self.store.act(board_id, 4, "withdraw", now=1003)
        self.assertEqual(self.store.members(board_id), [])
        self.store.act(board_id, 4, "vote", 0, now=1004)
        self.store = CommunityStore(self.path)
        self.assertEqual(len(self.store.boards(recover=True)), 1)
        with self.assertRaises(ValueError):
            self.store.act(board_id, 5, "vote", 0, now=2000)
        self.store.advance(now=2200)
        self.store.advance(now=2201)
        jobs = self.store.jobs(now=2201)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["payload"]["kind"], "board_finish")
        self.assertEqual(self.store.board(board_id)["status"], "closed")

    def test_group_capacity_under_competing_connections_and_cancel(self):
        board_id = self.board("group", capacity=2)
        def join(user_id):
            try:
                CommunityStore(self.path).act(board_id, user_id, "join", now=1001)
                return True
            except ValueError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(join, range(4, 8))), 1)
        self.assertEqual(len(self.store.members(board_id)), 2)
        self.store.advance(now=1500)
        self.store.finish_board(board_id)
        self.store.advance(now=2500)
        jobs = self.store.jobs(now=2500)
        self.assertEqual([job["payload"]["kind"] for job in jobs], ["board_finish"])
        self.assertEqual(len(jobs[0]["payload"]["user_ids"]), 2)
        self.assertEqual(self.store.board(board_id)["status"], "cancelled")

    def test_group_start_supersedes_late_pre_reminder(self):
        board_id = self.board("group")
        self.store.advance(now=1500)
        self.assertEqual(self.store.jobs(now=1500)[0]["payload"]["kind"], "group_before")
        self.store = CommunityStore(self.path)
        self.store.advance(now=3000)
        self.assertEqual([job["payload"]["kind"] for job in self.store.jobs(now=3000)], ["board_finish"])
        self.assertEqual(self.store.board(board_id)["status"], "started")

    def test_reminder_cancel_limits_and_reboot(self):
        ids = [self.store.create_reminder(1, 2, 3, "hello", 1100, now=1000) for _ in range(20)]
        with self.assertRaises(ValueError):
            self.store.create_reminder(1, 2, 3, "overflow", 1100, now=1000)
        self.store.cancel_reminder(ids[0])
        self.store = CommunityStore(self.path)
        self.store.advance(now=1200)
        self.store.cancel_reminder(ids[1])
        self.assertEqual(len(self.store.reminders(1, 3)), 18)
        self.assertEqual(self.store.job_for(f"reminder:{ids[1]}")["status"], "cancelled")
        self.store.delivered(self.store.job_for(f"reminder:{ids[1]}")["id"])
        self.assertEqual(self.store.reminder(ids[1])["status"], "cancelled")

    def test_release_dedup_and_partial_delivery_progress_survive_reboot(self):
        self.assertTrue(self.store.release("v1", 2, "notes"))
        job = self.store.jobs()[0]
        self.store.render(job["id"], {"parts":["a", "b"], "user_ids":[]})
        self.store.part_sent(job["id"], 123)
        self.store.failed(job["id"], "failure", now=1000)
        self.store = CommunityStore(self.path)
        self.assertFalse(self.store.release("v1", 999, "different"))
        restored = self.store.job(job["id"])
        self.assertEqual(restored["progress"], 1)
        self.assertEqual(restored["rendered"]["parts"], ["a", "b"])
        self.store.part_sent(job["id"], 124)
        self.store.delivered(job["id"])
        self.assertEqual(self.store.jobs(), [])
        with self.store.connect() as conn:
            release = conn.execute("SELECT * FROM release_announcements").fetchone()
            self.assertIsNotNone(release["announced_at"])
            self.assertEqual(json.loads(release["message_ids"]), [123, 124])


class CommunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = CommunityStore(Path(self.temp.name) / "state.db")
        self.bot = SimpleNamespace(get_cog=Mock(return_value=None), add_view=Mock(), is_owner=AsyncMock(return_value=False))
        with patch.object(module, "CommunityStore", return_value=self.store):
            self.cog = Community(self.bot)
        self.channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=123)), get_partial_message=Mock(return_value=SimpleNamespace(edit=AsyncMock())))
        self.cog.channel = AsyncMock(return_value=self.channel)

    async def asyncTearDown(self):
        for view in self.cog.views.values():
            view.stop()
        self.temp.cleanup()

    def interaction(self, user=4, message=123, guild=1):
        return SimpleNamespace(guild_id=guild, channel_id=2, message=SimpleNamespace(id=message), user=SimpleNamespace(id=user,bot=False), permissions=SimpleNamespace(manage_messages=False), response=SimpleNamespace(defer=AsyncMock(), is_done=Mock(return_value=True)), followup=SimpleNamespace(send=AsyncMock()))

    async def make_board(self, **kwargs):
        board_id = self.store.create_board("poll", 1, 2, 3, "poll", 9999999999, options=["😀" * 60, "b"], **kwargs)
        await self.cog.dispatch(self.store.job_for(f"publish:{board_id}")["id"])
        return board_id

    async def test_persistent_views_serialized_restore_and_permission_binding(self):
        board_id = await self.make_board()
        view = self.cog.views[board_id]
        self.assertTrue(view.is_persistent())
        self.assertTrue(all(len(item.custom_id) <= 100 for item in view.children))
        self.assertTrue(all(len(item.label.encode("utf-16-le")) // 2 <= 80 for item in view.children))
        tree = SimpleNamespace(allowed_contexts=discord.app_commands.AppCommandContext(), allowed_installs=discord.app_commands.AppInstallationType())
        serialized = [cmd.to_dict(tree) for cmd in self.cog.get_app_commands()]
        self.assertEqual(len(serialized), 6)
        before = len(self.store.members(board_id))
        await self.cog.handle_action(self.interaction(message=999), board_id, "vote", 0)
        self.assertEqual(len(self.store.members(board_id)), before)
        await self.cog.handle_action(self.interaction(), board_id, "end", None)
        self.assertEqual(self.store.board(board_id)["status"], "active")
        await self.cog.handle_action(self.interaction(), board_id, "vote", 1)
        self.assertEqual(self.store.members(board_id)[0]["choice"], 1)
        await self.cog.handle_action(self.interaction(user=3), board_id, "end", None)
        self.assertEqual(self.store.board(board_id)["status"], "closed")
        self.assertNotIn(board_id, self.cog.views)

    async def test_partial_send_retry_resumes_without_second_ai_call_or_pings(self):
        ai = SimpleNamespace(client=object(), read_default_persona=Mock(return_value="DEFAULT"), get_persona_for_channel=Mock(return_value=("CHANNEL", "id")), generate=AsyncMock(return_value=SimpleNamespace(text="@everyone 開場白")))
        self.bot.get_cog.return_value = ai
        self.store.release("v1", 2, "@everyone " + "更新\n" * 1500)
        job_id = self.store.jobs()[0]["id"]
        self.channel.send.side_effect = [SimpleNamespace(id=101), RuntimeError("disconnected")]
        with self.assertLogs(module.log, level="ERROR"):
            await self.cog.dispatch(job_id)
        job = self.store.job(job_id)
        self.assertEqual(job["progress"], 1)
        self.assertEqual(job["status"], "pending")
        ai.read_default_persona.assert_called_once()
        ai.get_persona_for_channel.assert_not_called()
        self.assertIn("開場白", job["rendered"]["parts"][0])
        self.assertNotIn("更新好啦", job["rendered"]["parts"][0])
        self.assertFalse(ai.generate.call_args.kwargs["enable_search"])
        with self.store.connect() as conn:
            conn.execute("UPDATE community_outbox SET retry_at=0 WHERE id=?", (job_id,))
        self.channel.send.side_effect = None
        await asyncio.gather(self.cog.dispatch(job_id), self.cog.dispatch(job_id))
        self.assertEqual(ai.generate.await_count, 1)
        self.assertEqual(self.store.job(job_id)["status"], "delivered")
        for call in self.channel.send.call_args_list:
            self.assertFalse(call.kwargs["allowed_mentions"].everyone)
            self.assertFalse(call.kwargs["allowed_mentions"].roles)
            self.assertNotIn("@everyone", call.args[0])
        self.assertEqual(self.channel.send.await_count, len(job["rendered"]["parts"]) + 1)

    async def test_reminder_persona_fallback_preserves_exact_content(self):
        ai = SimpleNamespace(client=object(), get_persona_for_channel=Mock(return_value=("CHANNEL", "id")), generate=AsyncMock(side_effect=RuntimeError("offline")))
        self.bot.get_cog.return_value = ai
        reminder_id = self.store.create_reminder(1, 2, 3, "吃拉麵 @everyone", 1000)
        self.store.advance()
        job = self.store.job_for(f"reminder:{reminder_id}")
        with self.assertLogs(module.log, level="ERROR"):
            await self.cog.dispatch(job["id"])
        content = self.channel.send.call_args.args[0]
        self.assertIn("吃拉麵", content)
        self.assertIn("<@3>", content)
        self.assertEqual([user.id for user in self.channel.send.call_args.kwargs["allowed_mentions"].users], [3])
        ai.get_persona_for_channel.assert_called_once_with(2)
        self.assertEqual(self.store.reminder(reminder_id)["status"], "sent")

    async def test_notification_uses_real_ai_generate_response_contract(self):
        ai = ai_module.AIChat.__new__(ai_module.AIChat)
        response = SimpleNamespace(text="社畜下班啦，該吃拉麵了！")
        request = AsyncMock(return_value=response)
        ai.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=request)))
        ai.request_slots = asyncio.Semaphore(3)
        ai.get_persona_for_channel = Mock(return_value=("CHANNEL PERSONA", "template"))
        self.bot.get_cog.return_value = ai
        reminder_id = self.store.create_reminder(1, 2, 3, "吃拉麵", 1000)
        self.store.advance()
        with patch.object(ai_module, "load_settings", return_value={"gemini_model":"gemini-3.8-flash", "ai_search_enabled":True, "ai_max_output_tokens":2048}):
            await self.cog.dispatch(self.store.job_for(f"reminder:{reminder_id}")["id"])
        self.assertTrue(self.channel.send.call_args.args[0].startswith(response.text))
        self.assertEqual(request.call_args.kwargs["config"].system_instruction, "CHANNEL PERSONA")
        self.assertIsNone(request.call_args.kwargs["config"].tools)

    async def test_large_group_mentions_are_split_into_valid_allowlists(self):
        board_id = self.store.create_board("group", 1, 2, 111111111111111111, "large group", 2000, now=1000)
        publish = self.store.job_for(f"publish:{board_id}")
        self.store.part_sent(publish["id"], 123)
        self.store.delivered(publish["id"], now=1000)
        for user_id in range(222222222222222222, 222222222222222372):
            self.store.act(board_id, user_id, "join", now=1001)
        self.store.advance(now=3000)
        await self.cog.dispatch(self.store.job_for(f"finish:{board_id}")["id"])
        calls = self.channel.send.call_args_list
        self.assertGreater(len(calls), 1)
        all_ids = set()
        for call in calls:
            users = call.kwargs["allowed_mentions"].users
            self.assertLessEqual(len(users), 100)
            self.assertLessEqual(len(call.args[0].encode("utf-16-le")) // 2, 1900)
            all_ids.update(user.id for user in users)
        self.assertEqual(len(all_ids), 151)

    async def test_cancel_permission_and_release_manifest_containment(self):
        reminder_id = self.store.create_reminder(1, 2, 3, "hello", 1000)
        await Community.cancel.callback(self.cog, self.interaction(), reminder_id)
        self.assertEqual(self.store.reminder(reminder_id)["status"], "pending")
        await Community.cancel.callback(self.cog, self.interaction(user=3, guild=999), reminder_id)
        self.assertEqual(self.store.reminder(reminder_id)["status"], "pending")
        await Community.cancel.callback(self.cog, self.interaction(user=3), reminder_id)
        self.assertEqual(self.store.reminder(reminder_id)["status"], "cancelled")
        root = Path(self.temp.name)
        (root / "release.json").write_text(json.dumps({"version":"v1", "notes_file":"../outside.md"}), encoding="utf-8")
        with patch.object(module, "BASE_DIR", root), self.assertRaises(ValueError):
            self.cog.load_release()

    async def test_embed_limits_with_long_emoji_and_tied_options(self):
        options = ["😀" * 59 + str(index) for index in range(10)]
        board_id = self.store.create_board("poll", 1, 2, 3, "*" * 200, 9999999999, options=options)
        job = self.store.job_for(f"publish:{board_id}")
        await self.cog.dispatch(job["id"])
        for index in range(10):
            self.store.act(board_id, index + 10, "vote", index)
        self.store.finish_board(board_id)
        embed = board_embed(self.store.board(board_id), self.store.members(board_id)).to_dict()
        self.assertLessEqual(len(embed["title"].encode("utf-16-le")) // 2, 256)
        self.assertLessEqual(len(embed["description"].encode("utf-16-le")) // 2, 4096)
        for field in embed["fields"]:
            self.assertLessEqual(len(field["value"].encode("utf-16-le")) // 2, 1024)
