"""Durable activities and notification outbox in the existing state database."""
import json
import sqlite3
import time
from contextlib import contextmanager

from config import BOT_STATE_DB_PATH


class CommunityStore:
    def __init__(self, path=BOT_STATE_DB_PATH):
        self.path = path
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS community_boards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL, title TEXT NOT NULL,
                    options TEXT NOT NULL DEFAULT '[]', details TEXT NOT NULL DEFAULT '',
                    due_at REAL NOT NULL, capacity INTEGER NOT NULL DEFAULT 0,
                    lead_minutes INTEGER NOT NULL DEFAULT 10, message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'creating', created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_members (
                    board_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    choice INTEGER NOT NULL DEFAULT -1, joined_at REAL NOT NULL,
                    PRIMARY KEY(board_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS community_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    content TEXT NOT NULL, due_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS community_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE,
                    channel_id INTEGER NOT NULL, payload TEXT NOT NULL,
                    rendered TEXT, progress INTEGER NOT NULL DEFAULT 0,
                    message_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS release_announcements (
                    version TEXT PRIMARY KEY, channel_id INTEGER NOT NULL,
                    announced_at REAL, message_ids TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS community_outbox_pending ON community_outbox(status, retry_at);
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def queue(conn, key, channel_id, payload):
        conn.execute("INSERT OR IGNORE INTO community_outbox(event_key, channel_id, payload) VALUES (?, ?, ?)", (key, channel_id, json.dumps(payload, ensure_ascii=False)))

    def create_board(self, kind, guild_id, channel_id, creator_id, title, due_at, *, options=None, details="", capacity=0, lead_minutes=10, now=None):
        if kind not in ("poll", "group"):
            raise ValueError("活動類型無效")
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) FROM community_boards WHERE creator_id=? AND status IN ('creating','active')", (creator_id,)).fetchone()[0]
            if count >= 10:
                raise ValueError("每人最多同時建立 10 個未結束活動")
            cursor = conn.execute("INSERT INTO community_boards(kind,guild_id,channel_id,creator_id,title,options,details,due_at,capacity,lead_minutes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (kind,guild_id,channel_id,creator_id,title,json.dumps(options or [],ensure_ascii=False),details,due_at,capacity,lead_minutes,now))
            board_id = cursor.lastrowid
            if kind == "group":
                conn.execute("INSERT INTO community_members(board_id,user_id,joined_at) VALUES (?,?,?)", (board_id,creator_id,now))
            self.queue(conn, f"publish:{board_id}", channel_id, {"kind":"publish", "board_id":board_id})
        return board_id

    def board(self, board_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM community_boards WHERE id=?", (board_id,)).fetchone()
        if not row:
            raise ValueError("找不到這個活動")
        board = dict(row)
        board["options"] = json.loads(board["options"])
        return board

    def boards(self, guild_id=None, channel_id=None, *, recover=False):
        query = "SELECT id FROM community_boards WHERE status IN ('creating','active')"
        params = []
        if recover:
            query = "SELECT id FROM community_boards WHERE message_id IS NOT NULL AND status='active'"
        if guild_id is not None:
            query += " AND guild_id=?"
            params.append(guild_id)
        if channel_id is not None:
            query += " AND channel_id=?"
            params.append(channel_id)
        with self.connect() as conn:
            ids = [row[0] for row in conn.execute(query + " ORDER BY due_at", params)]
        return [self.board(board_id) for board_id in ids]

    def members(self, board_id):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT user_id,choice,joined_at FROM community_members WHERE board_id=? ORDER BY joined_at,user_id", (board_id,))]

    def act(self, board_id, user_id, action, choice=None, *, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM community_boards WHERE id=?", (board_id,)).fetchone()
            if not row or row["status"] != "active" or row["due_at"] <= now:
                raise ValueError("活動已結束，不能再變更")
            if action == "vote" and row["kind"] == "poll":
                if not isinstance(choice, int) or not 0 <= choice < len(json.loads(row["options"])):
                    raise ValueError("投票選項無效")
                conn.execute("INSERT INTO community_members(board_id,user_id,choice,joined_at) VALUES (?,?,?,?) ON CONFLICT(board_id,user_id) DO UPDATE SET choice=excluded.choice", (board_id,user_id,choice,now))
            elif action == "join" and row["kind"] == "group":
                exists = conn.execute("SELECT 1 FROM community_members WHERE board_id=? AND user_id=?", (board_id,user_id)).fetchone()
                count = conn.execute("SELECT COUNT(*) FROM community_members WHERE board_id=?", (board_id,)).fetchone()[0]
                if not exists and row["capacity"] and count >= row["capacity"]:
                    raise ValueError("這團已滿，請等有人退出")
                conn.execute("INSERT OR IGNORE INTO community_members(board_id,user_id,joined_at) VALUES (?,?,?)", (board_id,user_id,now))
            elif (action == "leave" and row["kind"] == "group") or (action == "withdraw" and row["kind"] == "poll"):
                conn.execute("DELETE FROM community_members WHERE board_id=? AND user_id=?", (board_id,user_id))
            else:
                raise ValueError("這個活動不支援此操作")

    def finish_board(self, board_id, *, cancel=False):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM community_boards WHERE id=?", (board_id,)).fetchone()
            if not row or row["status"] not in ("active", "creating"):
                raise ValueError("活動已結束")
            status = "cancelled" if cancel or row["kind"] == "group" else "closed"
            conn.execute("UPDATE community_boards SET status=? WHERE id=?", (status,board_id))
            conn.execute("UPDATE community_outbox SET status='cancelled' WHERE event_key=? AND status='pending'", (f"before:{board_id}",))
            members = [user[0] for user in conn.execute("SELECT user_id FROM community_members WHERE board_id=? ORDER BY joined_at,user_id", (board_id,))]
            self.queue(conn, f"finish:{board_id}", row["channel_id"], {"kind":"board_finish", "board_id":board_id, "user_ids":members if row["kind"] == "group" else []})

    def create_reminder(self, guild_id, channel_id, user_id, content, due_at, *, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) FROM community_reminders WHERE user_id=? AND status IN ('pending','queued')", (user_id,)).fetchone()[0]
            if count >= 20:
                raise ValueError("每人最多保留 20 個未發送提醒")
            return conn.execute("INSERT INTO community_reminders(guild_id,channel_id,user_id,content,due_at,created_at) VALUES (?,?,?,?,?,?)", (guild_id,channel_id,user_id,content,due_at,now)).lastrowid

    def reminders(self, guild_id, user_id):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM community_reminders WHERE guild_id=? AND user_id=? AND status IN ('pending','queued') ORDER BY due_at", (guild_id,user_id))]

    def reminder(self, reminder_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM community_reminders WHERE id=?", (reminder_id,)).fetchone()
        if not row:
            raise ValueError("找不到這個提醒")
        return dict(row)

    def cancel_reminder(self, reminder_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("UPDATE community_reminders SET status='cancelled' WHERE id=? AND status IN ('pending','queued')", (reminder_id,)).rowcount
            if not changed:
                raise ValueError("提醒已發送或已取消")
            conn.execute("UPDATE community_outbox SET status='cancelled' WHERE event_key=? AND status='pending'", (f"reminder:{reminder_id}",))

    def advance(self, *, now=None):
        """Atomically freeze due polls and enqueue reminders; catches up after reboot."""
        now = time.time() if now is None else now
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute("SELECT * FROM community_reminders WHERE status='pending' AND due_at<=?", (now,)).fetchall():
                self.queue(conn, f"reminder:{row['id']}", row["channel_id"], {"kind":"reminder", "reminder_id":row["id"], "user_ids":[row["user_id"]], "content":row["content"], "due_at":row["due_at"]})
                conn.execute("UPDATE community_reminders SET status='queued' WHERE id=?", (row["id"],))
            for row in conn.execute("SELECT * FROM community_boards WHERE status='active'").fetchall():
                members = [user[0] for user in conn.execute("SELECT user_id FROM community_members WHERE board_id=? ORDER BY joined_at,user_id", (row["id"],))]
                if row["due_at"] <= now:
                    conn.execute("UPDATE community_boards SET status=? WHERE id=?", ("closed" if row["kind"] == "poll" else "started",row["id"]))
                    self.queue(conn, f"finish:{row['id']}", row["channel_id"], {"kind":"board_finish", "board_id":row["id"], "user_ids":members if row["kind"] == "group" else []})
                    # A late reboot sends the start notification, not an obsolete pre-reminder.
                    conn.execute("UPDATE community_outbox SET status='cancelled' WHERE event_key=? AND status='pending'", (f"before:{row['id']}",))
                elif row["kind"] == "group" and row["lead_minutes"] > 0:
                    before = row["due_at"] - row["lead_minutes"] * 60
                    if row["created_at"] < before <= now:
                        self.queue(conn, f"before:{row['id']}", row["channel_id"], {"kind":"group_before", "board_id":row["id"], "user_ids":members})

    def release(self, version, channel_id, notes):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            created = conn.execute("INSERT OR IGNORE INTO release_announcements(version,channel_id) VALUES (?,?)", (version,channel_id)).rowcount
            if created:
                self.queue(conn, f"release:{version}", channel_id, {"kind":"release", "version":version, "content":notes, "user_ids":[]})
        return bool(created)

    def jobs(self, *, now=None):
        with self.connect() as conn:
            return [self.job(row[0]) for row in conn.execute("SELECT id FROM community_outbox WHERE status='pending' AND retry_at<=? ORDER BY id LIMIT 10", (time.time() if now is None else now,))]

    def job(self, job_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM community_outbox WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("找不到通知工作")
        job = dict(row)
        for key in ("payload", "rendered", "message_ids"):
            job[key] = json.loads(job[key]) if job[key] is not None else None
        return job

    def job_for(self, key):
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM community_outbox WHERE event_key=?", (key,)).fetchone()
        return self.job(row[0]) if row else None

    def render(self, job_id, rendered):
        with self.connect() as conn:
            conn.execute("UPDATE community_outbox SET rendered=? WHERE id=? AND rendered IS NULL AND status='pending'", (json.dumps(rendered,ensure_ascii=False),job_id))

    def part_sent(self, job_id, message_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM community_outbox WHERE id=?", (job_id,)).fetchone()
            ids = json.loads(row["message_ids"]) + [message_id]
            conn.execute("UPDATE community_outbox SET progress=progress+1,message_ids=? WHERE id=?", (json.dumps(ids),job_id))

    def delivered(self, job_id, *, now=None):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM community_outbox WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != "pending":
                return
            payload = json.loads(row["payload"])
            ids = json.loads(row["message_ids"])
            conn.execute("UPDATE community_outbox SET status='delivered',last_error='' WHERE id=? AND status='pending'", (job_id,))
            if payload["kind"] == "publish" and ids:
                board = conn.execute("SELECT * FROM community_boards WHERE id=?", (payload["board_id"],)).fetchone()
                status = board["status"]
                if status == "creating":
                    status = "active" if board["due_at"] > (time.time() if now is None else now) else ("closed" if board["kind"] == "poll" else "started")
                conn.execute("UPDATE community_boards SET message_id=?,status=? WHERE id=?", (ids[0],status,board["id"]))
            elif payload["kind"] == "reminder":
                conn.execute("UPDATE community_reminders SET status='sent' WHERE id=? AND status='queued'", (payload["reminder_id"],))
            elif payload["kind"] == "release":
                conn.execute("UPDATE release_announcements SET announced_at=?,message_ids=? WHERE version=?", (time.time() if now is None else now,json.dumps(ids),payload["version"]))

    def failed(self, job_id, error, *, now=None):
        now = time.time() if now is None else now
        with self.connect() as conn:
            row = conn.execute("SELECT attempts FROM community_outbox WHERE id=?", (job_id,)).fetchone()
            attempts = row[0] + 1
            conn.execute("UPDATE community_outbox SET attempts=?,retry_at=?,last_error=? WHERE id=? AND status='pending'", (attempts,now + min(300,5 * 2 ** min(attempts,6)),error[:200],job_id))
