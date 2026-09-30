import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from services.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "chat.db"
        self.store = MemoryStore(self.path)

    def test_backlog_summarizes_oldest_and_preserves_other_rows(self):
        for index in range(85):
            self.store.add(1, 10, f"turn {index}")
        self.store.add(2, 20, "other channel")
        rows, summary, version = self.store.batch(1)
        self.assertEqual(rows[0][2], "turn 0")
        self.assertEqual(rows[-1][2], "turn 29")
        self.store.add(1, 10, "arrived during generation")
        self.assertTrue(self.store.commit_batch(1, rows, "summary", version))
        remaining = self.store.recent(1, 100)
        self.assertEqual(len(remaining), 56)
        self.assertEqual(remaining[0][2], "turn 30")
        self.assertEqual(remaining[-1][2], "arrived during generation")
        self.assertEqual(self.store.recent(2)[0][2], "other channel")

    def test_clear_during_generation_cannot_resurrect_summary(self):
        self.store.add(1, 10, "old")
        rows, _, version = self.store.batch(1)
        self.store.clear(1)
        self.store.add(1, 10, "new")
        self.assertFalse(self.store.commit_batch(1, rows, "stale", version))
        self.assertEqual(self.store.summary(1), "")
        self.assertEqual(self.store.recent(1)[0][2], "new")

    def test_manual_summary_and_duplicate_tasks_do_not_overwrite(self):
        self.store.add(1, 10, "old")
        rows, _, version = self.store.batch(1)
        self.store.save_summary(1, "owner's correction")
        self.assertFalse(self.store.commit_batch(1, rows, "stale", version))
        self.assertEqual(self.store.summary(1), "owner's correction")
        rows, _, version = self.store.batch(1)
        self.assertTrue(self.store.commit_batch(1, rows, "fresh", version))
        self.assertFalse(self.store.commit_batch(1, rows, "duplicate", version))
        self.assertEqual(self.store.summary(1), "fresh")

    def test_empty_output_preserves_history(self):
        self.store.add(1, 10, "important")
        rows, _, version = self.store.batch(1)
        self.assertFalse(self.store.commit_batch(1, rows, "  ", version))
        self.assertEqual(len(self.store.recent(1)), 1)

    def test_original_schema_migrates_without_losing_history(self):
        old_path = Path(self.directory.name) / "legacy.db"
        with closing(sqlite3.connect(old_path)) as conn:
            conn.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, message TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("INSERT INTO history(channel_id, message) VALUES (1, '[name]: original')")
            conn.execute("CREATE TABLE summaries(channel_id INTEGER PRIMARY KEY, summary_text TEXT)")
            conn.execute("INSERT INTO summaries VALUES (1, 'original summary')")
            conn.commit()
        migrated = MemoryStore(old_path)
        self.assertEqual(migrated.recent(1)[0][1:], (None, "[name]: original"))
        self.assertEqual(migrated.summary(1), "original summary")
