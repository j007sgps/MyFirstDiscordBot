"""Small SQLite transactions; never delete rows absent from a summary batch."""
import sqlite3
from contextlib import contextmanager
from config import CHAT_DB_PATH


class MemoryStore:
    def __init__(self, path=CHAT_DB_PATH):
        self.path = path
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, message TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
            if "user_id" not in columns:
                conn.execute("ALTER TABLE history ADD COLUMN user_id INTEGER")
            conn.execute("CREATE TABLE IF NOT EXISTS summaries (channel_id INTEGER PRIMARY KEY, summary_text TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS memory_versions (channel_id INTEGER PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE INDEX IF NOT EXISTS history_channel_id ON history(channel_id, id)")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, channel_id, user_id, content):
        with self.connect() as conn:
            conn.execute("INSERT INTO history(channel_id, user_id, message) VALUES (?, ?, ?)", (channel_id, user_id, content))

    def add_turn(self, channel_id, user_id, user_text, bot_id, reply):
        with self.connect() as conn:
            conn.executemany("INSERT INTO history(channel_id, user_id, message) VALUES (?, ?, ?)", [(channel_id, user_id, user_text), (channel_id, bot_id, reply)])

    def recent(self, channel_id, limit=50):
        with self.connect() as conn:
            rows = conn.execute("SELECT id, user_id, message FROM history WHERE channel_id=? ORDER BY id DESC LIMIT ?", (channel_id, limit)).fetchall()
        return list(reversed(rows))

    def summary(self, channel_id):
        with self.connect() as conn:
            row = conn.execute("SELECT summary_text FROM summaries WHERE channel_id=?", (channel_id,)).fetchone()
        return row[0] if row else ""

    @staticmethod
    def _bump(conn, channel_id):
        conn.execute("INSERT INTO memory_versions(channel_id, version) VALUES (?, 1) ON CONFLICT(channel_id) DO UPDATE SET version=version+1", (channel_id,))

    def save_summary(self, channel_id, summary):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO summaries VALUES (?, ?)", (channel_id, summary))
            self._bump(conn, channel_id)

    def clear(self, channel_id):
        with self.connect() as conn:
            conn.execute("DELETE FROM history WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM summaries WHERE channel_id=?", (channel_id,))
            self._bump(conn, channel_id)

    def batch(self, channel_id, limit=30):
        with self.connect() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("SELECT id, user_id, message FROM history WHERE channel_id=? ORDER BY id LIMIT ?", (channel_id, limit)).fetchall()
            summary = conn.execute("SELECT summary_text FROM summaries WHERE channel_id=?", (channel_id,)).fetchone()
            version = conn.execute("SELECT version FROM memory_versions WHERE channel_id=?", (channel_id,)).fetchone()
        return rows, summary[0] if summary else "", version[0] if version else 0

    def commit_batch(self, channel_id, rows, summary, expected_version):
        if not rows or not summary.strip():
            return False
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("SELECT version FROM memory_versions WHERE channel_id=?", (channel_id,)).fetchone()
            if (version[0] if version else 0) != expected_version:
                return False
            conn.execute("INSERT OR REPLACE INTO summaries VALUES (?, ?)", (channel_id, summary))
            conn.executemany("DELETE FROM history WHERE channel_id=? AND id=?", [(channel_id, row[0]) for row in rows])
            self._bump(conn, channel_id)
        return True
