import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.youtube import YouTubeTracker
from config import DEFAULT_SETTINGS
from services.notification_state import NotificationState
from services.youtube_posts import CommunityPost


def video(item_id):
    return SimpleNamespace(id=item_id, title=item_id, link="https://example.com/" + item_id, published="today")


class NotificationStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.db"
        self.state = NotificationState(self.path)

    def test_baseline_restart_multi_upload_and_channel_isolation(self):
        self.assertEqual(self.state.prepare("A", "videos", [video("v1"), video("v0")]), [])
        state = NotificationState(self.path)
        pending = state.prepare("A", "videos", [video("v3"), video("v2"), video("v1")])
        self.assertEqual([item.id for item in pending], ["v3", "v2"])
        for item in pending:
            state.delivered("A", "videos", item)
        self.assertEqual(state.prepare("A", "videos", pending), [])
        self.assertEqual(state.prepare("B", "videos", [video("b1")]), [])
        self.assertEqual(state.counts()["videos"], 2)

    def test_legacy_anchor_only_seeds_older_entries(self):
        with self.state.connect() as conn:
            conn.execute("INSERT INTO youtube_notified(video_id) VALUES ('v1')")
        pending = self.state.prepare("A", "videos", [video("v3"), video("v2"), video("v1"), video("v0")])
        self.assertEqual([item.id for item in pending], ["v3", "v2"])

    def test_posts_have_independent_empty_baseline_and_edit_deduplication(self):
        self.state.prepare("A", "posts", [])
        post = CommunityPost("Ug1", "text", "author", "today")
        self.assertEqual(self.state.prepare("A", "posts", [post]), [post])
        self.state.delivered("A", "posts", post)
        edited = CommunityPost("Ug1", "edited text", "author", "today")
        self.assertEqual(self.state.prepare("A", "posts", [edited]), [])


class PollingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        state = NotificationState(Path(self.directory.name) / "state.db")
        with patch("cogs.youtube.NotificationState", return_value=state):
            self.cog = YouTubeTracker(SimpleNamespace())
        self.settings = DEFAULT_SETTINGS.copy()
        self.settings_patch = patch("cogs.youtube.load_settings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    async def test_failed_delivery_retries_without_marking_or_stopping_other_source(self):
        self.cog.state.prepare(self.settings["youtube_channel_id"], "videos", [video("old")])
        self.cog.state.prepare(self.settings["youtube_channel_id"], "posts", [])
        self.cog.parse_feed = AsyncMock(return_value=SimpleNamespace(entries=[video("new"), video("old")]))
        self.cog.fetch_posts = AsyncMock(side_effect=ValueError("page changed"))
        self.cog.send_notification = AsyncMock(side_effect=RuntimeError("delivery failed"))
        with self.assertLogs("cogs.youtube", level="ERROR"):
            result = await self.cog.check_all_once()
        self.assertIn("error", result["videos"])
        self.assertIn("error", result["posts"])
        self.assertNotIn("new", self.cog.state.known_ids(self.settings["youtube_channel_id"], "videos"))
        self.cog.send_notification = AsyncMock()
        with self.assertLogs("cogs.youtube", level="ERROR"):
            result = await self.cog.check_all_once()
        self.assertEqual(result["videos"]["sent"], 1)
        self.assertEqual(self.cog.health["videos"]["failures"], 0)

    async def test_concurrent_manual_checks_do_not_duplicate(self):
        self.settings["youtube_posts_enabled"] = False
        self.cog.state.prepare(self.settings["youtube_channel_id"], "videos", [video("old")])
        self.cog.parse_feed = AsyncMock(return_value=SimpleNamespace(entries=[video("new"), video("old")]))
        self.cog.send_notification = AsyncMock()
        await asyncio.gather(self.cog.check_all_once(), self.cog.check_all_once())
        self.cog.send_notification.assert_awaited_once()

    async def test_setting_changed_while_fetching_does_not_deliver_or_seed(self):
        settings = self.settings.copy()
        async def fetch(_):
            self.settings["discord_channel_id"] += 1
            return SimpleNamespace(entries=[video("new")])
        self.cog.parse_feed = fetch
        self.cog.send_notification = AsyncMock()
        result = await self.cog.check_source("videos", settings)
        self.assertIn("note", result)
        self.cog.send_notification.assert_not_awaited()
        self.assertEqual(self.cog.state.known_ids(settings["youtube_channel_id"], "videos"), set())
