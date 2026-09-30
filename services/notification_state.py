"""Persistent per-channel baselines, preserving the original video database."""
import sqlite3
from contextlib import contextmanager
from config import BOT_STATE_DB_PATH


class NotificationState:
    def __init__(self, path=BOT_STATE_DB_PATH):
        self.path = path
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS youtube_notified (video_id TEXT PRIMARY KEY, title TEXT, link TEXT, published TEXT, notified_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS youtube_sources (channel_id TEXT, source TEXT, initialized_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(channel_id, source))")
            conn.execute("CREATE TABLE IF NOT EXISTS youtube_items (channel_id TEXT, source TEXT, item_id TEXT, status TEXT NOT NULL, seen_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(channel_id, source, item_id))")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def known_ids(self, channel_id, source):
        with self.connect() as conn:
            return {row[0] for row in conn.execute("SELECT item_id FROM youtube_items WHERE channel_id=? AND source=?", (channel_id, source))}

    def prepare(self, channel_id, source, items):
        """Return unknown entries, newest first. Cold snapshots are never sent."""
        with self.connect() as conn:
            initialized = conn.execute("SELECT 1 FROM youtube_sources WHERE channel_id=? AND source=?", (channel_id, source)).fetchone()
            if not initialized:
                baseline = items
                if source == "videos":
                    legacy = {row[0] for row in conn.execute("SELECT video_id FROM youtube_notified")}
                    # Only import legacy state if a matching ID proves it belongs to this feed.
                    anchor = next((i for i, item in enumerate(items) if item.id in legacy), None)
                    if anchor is not None:
                        baseline = items[anchor:]
                conn.executemany("INSERT OR IGNORE INTO youtube_items(channel_id, source, item_id, status) VALUES (?, ?, ?, 'baseline')", [(channel_id, source, item.id) for item in baseline])
                conn.execute("INSERT INTO youtube_sources(channel_id, source) VALUES (?, ?)", (channel_id, source))
            known = {row[0] for row in conn.execute("SELECT item_id FROM youtube_items WHERE channel_id=? AND source=?", (channel_id, source))}
        return [item for item in items if item.id not in known]

    def delivered(self, channel_id, source, item):
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO youtube_items(channel_id, source, item_id, status) VALUES (?, ?, ?, 'sent')", (channel_id, source, item.id))
            if source == "videos":
                conn.execute("INSERT OR IGNORE INTO youtube_notified(video_id, title, link, published) VALUES (?, ?, ?, ?)", (item.id, getattr(item, "title", ""), item.link, getattr(item, "published", "")))

    def counts(self):
        with self.connect() as conn:
            return dict(conn.execute("SELECT source, COUNT(*) FROM youtube_items WHERE status='sent' GROUP BY source"))
