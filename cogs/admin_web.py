import hmac
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from aiohttp import web
from discord.ext import commands

from config import load_persona_store, load_settings, save_persona_store, save_settings


PERSONA_PATH = Path("shachiku.md")
CHAT_DB_PATH = Path("chat_history.db")
BOT_STATE_DB_PATH = Path("bot_state.db")


def count_sqlite_rows(db_path, table_name):
    if not Path(db_path).exists():
        return None

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            return cursor.fetchone()[0]
    except sqlite3.Error:
        return None


def parse_int(value, field_name, allow_zero=False):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"{field_name} must be an integer")

    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise web.HTTPBadRequest(text=f"{field_name} must be positive")
    return parsed


def ensure_memory_tables():
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                message TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                channel_id INTEGER PRIMARY KEY,
                summary_text TEXT
            )
            """
        )
        conn.commit()


class AdminWeb(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.host = os.getenv("ADMIN_HOST", "127.0.0.1")
        self.port = int(os.getenv("ADMIN_PORT", "8080"))
        self.admin_token = os.getenv("ADMIN_TOKEN", "")
        self.runner = None
        self.site = None
        self.bot.loop.create_task(self.start_server())

    def cog_unload(self):
        self.bot.loop.create_task(self.stop_server())

    async def start_server(self):
        app = web.Application(middlewares=[self.auth_middleware])
        app.router.add_get("/", self.handle_index)
        app.router.add_get("/api/status", self.handle_status)
        app.router.add_get("/api/settings", self.handle_get_settings)
        app.router.add_put("/api/settings", self.handle_put_settings)
        app.router.add_get("/api/channels", self.handle_channels)
        app.router.add_get("/api/persona", self.handle_get_persona)
        app.router.add_put("/api/persona", self.handle_put_persona)
        app.router.add_post("/api/persona/lab", self.handle_persona_lab)
        app.router.add_get("/api/personas", self.handle_get_personas)
        app.router.add_put("/api/personas/template", self.handle_put_persona_template)
        app.router.add_delete("/api/personas/template", self.handle_delete_persona_template)
        app.router.add_put("/api/personas/channel", self.handle_put_channel_persona)
        app.router.add_post("/api/reload", self.handle_reload)
        app.router.add_get("/api/youtube/latest", self.handle_youtube_latest)
        app.router.add_post("/api/youtube/check", self.handle_youtube_check)
        app.router.add_get("/api/memory/channels", self.handle_memory_channels)
        app.router.add_get("/api/memory", self.handle_get_memory)
        app.router.add_put("/api/memory/summary", self.handle_put_memory_summary)
        app.router.add_post("/api/memory/clear", self.handle_clear_memory)

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        try:
            await self.site.start()
        except OSError as e:
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            print(f"[AdminWeb] 管理面板啟動失敗：{e}")
            return

        if self.admin_token:
            print(f"[AdminWeb] 管理面板已啟動：http://{self.host}:{self.port}")
        else:
            print("[AdminWeb] 已啟動，但 ADMIN_TOKEN 未設定，API 會拒絕請求。")

    async def stop_server(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
            self.site = None

    @web.middleware
    async def auth_middleware(self, request, handler):
        if not request.path.startswith("/api/"):
            return await handler(request)

        if not self.admin_token:
            return web.json_response({"error": "ADMIN_TOKEN is not configured"}, status=503)

        auth_header = request.headers.get("Authorization", "")
        header_token = request.headers.get("X-Admin-Token", "")
        bearer_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        request_token = bearer_token or header_token or request.query.get("token", "")

        if not hmac.compare_digest(request_token, self.admin_token):
            return web.json_response({"error": "Unauthorized"}, status=401)

        return await handler(request)

    def json_response(self, data, status=200):
        return web.json_response(data, status=status)

    def get_gemini_model_name(self):
        ai_cog = self.bot.get_cog("AIChat")
        if not ai_cog or not hasattr(ai_cog, "model"):
            return "未載入"

        model_name = getattr(ai_cog.model, "model_name", "") or getattr(ai_cog.model, "_model_name", "")
        if model_name.startswith("models/"):
            model_name = model_name[7:]
        return model_name or "無法取得模型名稱"

    def get_channel_name(self, channel_id):
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            return ""
        return getattr(channel, "name", "") or str(channel)

    async def handle_index(self, request):
        return web.Response(text=ADMIN_HTML, content_type="text/html")

    async def handle_status(self, request):
        started_at = getattr(self.bot, "started_at", None)
        uptime_seconds = None
        if started_at:
            uptime_seconds = int((datetime.now(timezone.utc) - started_at).total_seconds())

        youtube_cog = self.bot.get_cog("YouTubeTracker")
        youtube_loop_running = bool(youtube_cog and youtube_cog.check_new_video.is_running())

        return self.json_response(
            {
                "bot_user": str(self.bot.user) if self.bot.user else "",
                "uptime_seconds": uptime_seconds,
                "latency_ms": round(self.bot.latency * 1000),
                "loaded_cogs": sorted(self.bot.extensions.keys()),
                "gemini_model": self.get_gemini_model_name(),
                "youtube_loop_running": youtube_loop_running,
                "secrets": {
                    "discord_token": bool(os.getenv("DISCORD_TOKEN")),
                    "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
                    "admin_token": bool(self.admin_token),
                },
                "database_counts": {
                    "history": count_sqlite_rows(CHAT_DB_PATH, "history"),
                    "summaries": count_sqlite_rows(CHAT_DB_PATH, "summaries"),
                    "youtube_notified": count_sqlite_rows(BOT_STATE_DB_PATH, "youtube_notified"),
                },
            }
        )

    async def handle_get_settings(self, request):
        return self.json_response(load_settings())

    async def handle_put_settings(self, request):
        payload = await request.json()
        settings = {
            "discord_channel_id": parse_int(payload.get("discord_channel_id"), "discord_channel_id"),
            "discord_role_id": parse_int(payload.get("discord_role_id", 0), "discord_role_id", allow_zero=True),
            "youtube_channel_id": str(payload.get("youtube_channel_id", "")).strip(),
            "youtube_check_minutes": float(payload.get("youtube_check_minutes", 5)),
        }
        if not settings["youtube_channel_id"]:
            raise web.HTTPBadRequest(text="youtube_channel_id is required")
        if settings["youtube_check_minutes"] < 0.1:
            raise web.HTTPBadRequest(text="youtube_check_minutes must be at least 0.1")

        saved = save_settings(settings)
        youtube_cog = self.bot.get_cog("YouTubeTracker")
        if youtube_cog:
            youtube_cog.apply_loop_interval()
        return self.json_response(saved)

    async def handle_channels(self, request):
        channels = []
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                channels.append(
                    {
                        "id": str(channel.id),
                        "name": channel.name,
                        "guild": guild.name,
                    }
                )
        channels.sort(key=lambda item: (item["guild"].lower(), item["name"].lower()))
        return self.json_response({"channels": channels})

    async def handle_get_persona(self, request):
        if not PERSONA_PATH.exists():
            return self.json_response({"content": ""})
        return self.json_response({"content": PERSONA_PATH.read_text(encoding="utf-8")})

    async def handle_put_persona(self, request):
        payload = await request.json()
        content = str(payload.get("content", ""))
        PERSONA_PATH.write_text(content, encoding="utf-8")
        return self.json_response({"ok": True, "bytes": len(content.encode("utf-8"))})

    async def handle_persona_lab(self, request):
        payload = await request.json()
        content = str(payload.get("content", "")).strip()
        message = str(payload.get("message", "")).strip()
        if not content:
            raise web.HTTPBadRequest(text="content is required")
        if not message:
            raise web.HTTPBadRequest(text="message is required")

        ai_cog = self.bot.get_cog("AIChat")
        if not ai_cog:
            return self.json_response({"error": "AIChat is not loaded"}, status=503)

        model = ai_cog.build_model(content)
        response = model.generate_content(message)
        return self.json_response({"ok": True, "reply": getattr(response, "text", "")})

    async def handle_get_personas(self, request):
        store = load_persona_store()
        assignments = []
        for channel_id, template_id in sorted(store.get("channel_personas", {}).items()):
            assignments.append(
                {
                    "channel_id": channel_id,
                    "channel_name": self.get_channel_name(channel_id),
                    "template_id": template_id,
                    "template_name": store.get("templates", {}).get(template_id, {}).get("name", template_id),
                }
            )
        return self.json_response(
            {
                "templates": store.get("templates", {}),
                "channel_personas": store.get("channel_personas", {}),
                "assignments": assignments,
            }
        )

    async def handle_put_persona_template(self, request):
        payload = await request.json()
        store = load_persona_store()
        template_id = str(payload.get("id", "")).strip() or f"persona-{uuid4().hex[:8]}"
        name = str(payload.get("name", "")).strip() or template_id
        content = str(payload.get("content", "")).strip()
        if not content:
            raise web.HTTPBadRequest(text="content is required")

        store.setdefault("templates", {})[template_id] = {
            "name": name,
            "content": content,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        saved = save_persona_store(store)
        return self.json_response({"ok": True, "id": template_id, "template": saved["templates"][template_id]})

    async def handle_delete_persona_template(self, request):
        template_id = str(request.query.get("id", "")).strip()
        if not template_id:
            raise web.HTTPBadRequest(text="id is required")

        store = load_persona_store()
        store.get("templates", {}).pop(template_id, None)
        store["channel_personas"] = {
            channel_id: assigned_id
            for channel_id, assigned_id in store.get("channel_personas", {}).items()
            if assigned_id != template_id
        }
        save_persona_store(store)
        return self.json_response({"ok": True, "id": template_id})

    async def handle_put_channel_persona(self, request):
        payload = await request.json()
        channel_id = str(parse_int(payload.get("channel_id"), "channel_id"))
        template_id = str(payload.get("template_id", "")).strip()
        store = load_persona_store()

        if template_id:
            if template_id not in store.get("templates", {}):
                raise web.HTTPBadRequest(text="template_id does not exist")
            store.setdefault("channel_personas", {})[channel_id] = template_id
        else:
            store.setdefault("channel_personas", {}).pop(channel_id, None)

        saved = save_persona_store(store)
        return self.json_response(
            {
                "ok": True,
                "channel_id": channel_id,
                "template_id": saved.get("channel_personas", {}).get(channel_id, ""),
            }
        )

    async def handle_reload(self, request):
        payload = await request.json()
        extension = str(payload.get("extension", "")).strip()
        if extension not in {"ai_chat", "youtube"}:
            raise web.HTTPBadRequest(text="extension must be ai_chat or youtube")

        await self.bot.reload_extension(f"cogs.{extension}")
        synced_commands = await self.bot.tree.sync()
        return self.json_response({"ok": True, "extension": extension, "synced_commands": len(synced_commands)})

    async def handle_youtube_latest(self, request):
        youtube_cog = self.bot.get_cog("YouTubeTracker")
        if not youtube_cog:
            return self.json_response({"error": "YouTubeTracker is not loaded"}, status=503)

        feed = youtube_cog.parse_feed()
        if not feed.entries:
            return self.json_response({"latest": None})

        latest = feed.entries[0]
        return self.json_response(
            {
                "latest": {
                    "id": getattr(latest, "id", ""),
                    "title": getattr(latest, "title", ""),
                    "link": getattr(latest, "link", ""),
                    "published": getattr(latest, "published", ""),
                }
            }
        )

    async def handle_youtube_check(self, request):
        youtube_cog = self.bot.get_cog("YouTubeTracker")
        if not youtube_cog:
            return self.json_response({"error": "YouTubeTracker is not loaded"}, status=503)

        latest = await youtube_cog.check_latest_video_once()
        if not latest:
            return self.json_response({"latest": None})
        return self.json_response(
            {
                "ok": True,
                "latest": {
                    "id": getattr(latest, "id", ""),
                    "title": getattr(latest, "title", ""),
                    "link": getattr(latest, "link", ""),
                },
            }
        )

    async def handle_memory_channels(self, request):
        ensure_memory_tables()
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT channel_id FROM summaries
                UNION
                SELECT channel_id FROM history
                ORDER BY channel_id
                """
            )
            channel_ids = [row[0] for row in cursor.fetchall()]

        return self.json_response(
            {
                "channels": [
                    {"id": channel_id, "name": self.get_channel_name(channel_id)}
                    for channel_id in channel_ids
                ]
            }
        )

    async def handle_get_memory(self, request):
        channel_id = parse_int(request.query.get("channel_id"), "channel_id")
        ensure_memory_tables()
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT summary_text FROM summaries WHERE channel_id = ?", (channel_id,))
            summary_row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM history WHERE channel_id = ?", (channel_id,))
            history_count = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT id, message, timestamp FROM history
                WHERE channel_id = ?
                ORDER BY id DESC
                LIMIT 10
                """,
                (channel_id,),
            )
            history_rows = list(reversed(cursor.fetchall()))

        return self.json_response(
            {
                "channel_id": channel_id,
                "channel_name": self.get_channel_name(channel_id),
                "summary_text": summary_row[0] if summary_row else "",
                "has_summary": bool(summary_row and summary_row[0]),
                "history_count": history_count,
                "history": [
                    {"id": row_id, "message": message, "timestamp": timestamp}
                    for row_id, message, timestamp in history_rows
                ],
            }
        )

    async def handle_put_memory_summary(self, request):
        payload = await request.json()
        channel_id = parse_int(payload.get("channel_id"), "channel_id")
        summary_text = str(payload.get("summary_text", ""))
        ensure_memory_tables()
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO summaries (channel_id, summary_text) VALUES (?, ?)",
                (channel_id, summary_text),
            )
            conn.commit()
        return self.json_response({"ok": True, "channel_id": channel_id, "bytes": len(summary_text.encode("utf-8"))})

    async def handle_clear_memory(self, request):
        payload = await request.json()
        channel_id = parse_int(payload.get("channel_id"), "channel_id")
        ensure_memory_tables()
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM history WHERE channel_id = ?", (channel_id,))
            cursor.execute("DELETE FROM summaries WHERE channel_id = ?", (channel_id,))
            conn.commit()
        return self.json_response({"ok": True, "channel_id": channel_id})


ADMIN_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bot Admin</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #10110f;
      --bg-2: #171812;
      --panel: rgba(31, 33, 25, .92);
      --panel-strong: #25271d;
      --text: #f4eedf;
      --muted: #aaa08d;
      --line: #3b3a2f;
      --line-bright: #575440;
      --accent: #42d392;
      --accent-dark: #1f9e69;
      --accent-soft: rgba(66, 211, 146, .14);
      --danger: #ff6b5f;
      --danger-soft: rgba(255, 107, 95, .14);
      --warn: #e6b450;
      --ink: #080a08;
      --mono: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
      --body: "Aptos", "Segoe UI", "Noto Sans TC", sans-serif;
    }
    * { box-sizing: border-box; }
    ::selection { background: rgba(66, 211, 146, .35); color: var(--text); }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(90deg, rgba(230, 180, 80, .05) 1px, transparent 1px) 0 0 / 76px 76px,
        linear-gradient(rgba(230, 180, 80, .035) 1px, transparent 1px) 0 0 / 76px 76px,
        radial-gradient(circle at 75% -10%, rgba(66, 211, 146, .13), transparent 34%),
        linear-gradient(135deg, var(--bg), #15130f 62%, #1d1b13);
      color: var(--text);
      font-family: var(--body);
      line-height: 1.45;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.025) 0 1px, transparent 1px 3px);
      mix-blend-mode: overlay;
      opacity: .5;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px clamp(18px, 4vw, 44px);
      border-bottom: 1px solid var(--line);
      background: rgba(16, 17, 15, .86);
      backdrop-filter: blur(18px);
      position: sticky;
      top: 0;
      z-index: 5;
      box-shadow: 0 16px 40px rgba(0,0,0,.22);
    }
    h1 {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(26px, 3vw, 42px);
      letter-spacing: 0;
      line-height: 1;
    }
    h1::after {
      content: " / control deck";
      color: var(--accent);
      font: 700 12px var(--mono);
      text-transform: uppercase;
    }
    h2 {
      margin: 0 0 14px;
      font-size: 13px;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--accent);
    }
    h3 { margin: 16px 0 8px; font-size: 15px; color: #efe4c8; }
    main {
      width: min(1320px, calc(100% - 28px));
      margin: 24px auto 56px;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 18px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 18px;
      box-shadow: 0 20px 70px rgba(0,0,0,.24), inset 0 1px 0 rgba(255,255,255,.04);
      position: relative;
      overflow: hidden;
    }
    section::before {
      content: "";
      position: absolute;
      inset: 0 0 auto;
      height: 3px;
      background: linear-gradient(90deg, var(--accent), var(--warn), transparent);
      opacity: .9;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin: 12px 0 0;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .span-2 { grid-column: span 2; }
    .span-4 { grid-column: span 4; }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 13px;
      min-height: 92px;
      background: linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.012));
      box-shadow: inset 0 1px 0 rgba(255,255,255,.04);
    }
    .metric b {
      display: block;
      font: 700 11px var(--mono);
      color: var(--muted);
      margin-bottom: 7px;
      text-transform: uppercase;
    }
    .metric span { font-size: 19px; font-weight: 800; overflow-wrap: anywhere; }
    label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font: 700 12px var(--mono);
      text-transform: uppercase;
    }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 5px;
      background: #11130f;
      color: var(--text);
      padding: 10px 11px;
      font: inherit;
      outline: none;
      transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
    }
    input:focus, textarea:focus, select:focus {
      border-color: var(--accent);
      background: #0d100d;
      box-shadow: 0 0 0 3px var(--accent-soft);
    }
    textarea {
      min-height: 230px;
      resize: vertical;
      font-family: var(--mono);
      font-size: 13px;
      line-height: 1.6;
    }
    #persona { min-height: 330px; }
    #persona-lab-message, #memory-summary { min-height: 190px; }
    button {
      border: 1px solid rgba(66, 211, 146, .8);
      background: linear-gradient(180deg, #51e6a3, #20a66b);
      color: #06100b;
      border-radius: 5px;
      padding: 10px 13px;
      font: 900 12px var(--mono);
      text-transform: uppercase;
      cursor: pointer;
      box-shadow: 0 8px 22px rgba(32, 166, 107, .22);
      transition: transform .16s ease, box-shadow .16s ease, filter .16s ease;
    }
    button:hover { transform: translateY(-1px); filter: brightness(1.06); }
    button:active { transform: translateY(0); }
    button.secondary {
      background: rgba(255,255,255,.035);
      color: var(--accent);
      box-shadow: none;
    }
    button.danger {
      background: linear-gradient(180deg, #ff897f, #c93429);
      border-color: var(--danger);
      color: #180302;
      box-shadow: 0 8px 22px rgba(255, 107, 95, .18);
    }
    button:disabled { opacity: .55; cursor: not-allowed; }
    pre {
      overflow: auto;
      background: #080a08;
      color: #dfe9d7;
      border: 1px solid #22291f;
      border-radius: 6px;
      padding: 13px;
      min-height: 88px;
      white-space: pre-wrap;
      font-family: var(--mono);
      box-shadow: inset 0 1px 18px rgba(0,0,0,.32);
    }
    .note { color: var(--muted); font-size: 13px; }
    .ok { color: var(--accent); font-weight: 700; }
    .bad { color: var(--danger); font-weight: 700; }
    .warn { color: var(--warn); font-weight: 700; }
    .history {
      display: grid;
      gap: 8px;
      max-height: 360px;
      overflow: auto;
    }
    .history-row {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: rgba(255,255,255,.035);
    }
    .history-row small { color: var(--muted); display: block; margin-bottom: 4px; }
    #token {
      width: min(360px, 52vw);
      font-family: var(--mono);
    }
    @media (max-width: 880px) {
      header { align-items: flex-start; flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      .span-2, .span-4 { grid-column: span 1; }
      #token { width: 100%; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Vibe Bot</h1>
      <div class="note">Private Discord bot operations console</div>
    </div>
    <div class="toolbar">
      <input id="token" type="password" placeholder="ADMIN_TOKEN">
      <button id="save-token" class="secondary">保存 Token</button>
      <button id="refresh">刷新</button>
    </div>
  </header>
  <main>
    <section>
      <h2>Dashboard</h2>
      <div id="status-grid" class="grid"></div>
    </section>

    <section>
      <h2>Settings</h2>
      <div class="grid">
        <label>Discord 通知頻道 ID<input id="discord_channel_id"></label>
        <label>Discord Role ID<input id="discord_role_id"></label>
        <label>YouTube Channel ID<input id="youtube_channel_id"></label>
        <label>檢查間隔（分鐘）<input id="youtube_check_minutes" type="number" min="0.1" step="0.1"></label>
      </div>
      <p class="toolbar"><button id="save-settings">儲存設定</button><span class="note">改完 YouTube/Discord 目標後，建議 reload youtube。</span></p>
    </section>

    <section>
      <h2>Persona</h2>
      <div class="grid">
        <label>人格版型<select id="persona-template-select"></select></label>
        <label>版型 ID<input id="persona-template-id" placeholder="留空會自動產生"></label>
        <label class="span-2">版型名稱<input id="persona-template-name" placeholder="例如：社畜 / 天才 / 冷靜助理"></label>
      </div>
      <textarea id="persona" spellcheck="false"></textarea>
      <p class="toolbar">
        <button id="save-persona">儲存預設 Persona</button>
        <button id="save-persona-template">保存為人格版型</button>
        <button id="delete-persona-template" class="danger">刪除版型</button>
        <button class="secondary" data-reload="ai_chat">Reload AI Chat</button>
      </p>
      <div class="grid">
        <label>選擇頻道<select id="persona-channel-select"></select></label>
        <label>頻道 ID<input id="persona-channel-id" placeholder="要指定人格的 Discord channel ID"></label>
        <label>指定人格<select id="persona-assign-template"></select></label>
        <div class="toolbar"><button id="assign-persona">指定頻道人格</button><button id="clear-persona-assignment" class="secondary">清除指定</button></div>
      </div>
      <pre id="persona-assignments"></pre>
      <h3>Persona Lab</h3>
      <textarea id="persona-lab-message" spellcheck="false" placeholder="輸入測試訊息。這裡只測試人格，不寫入 Discord 記憶。"></textarea>
      <p class="toolbar"><button id="run-persona-lab">測試人格回覆</button></p>
      <pre id="persona-lab-output"></pre>
    </section>

    <section>
      <h2>Actions</h2>
      <div class="toolbar">
        <button class="secondary" data-reload="youtube">Reload YouTube</button>
        <button class="secondary" data-reload="ai_chat">Reload AI Chat</button>
        <button id="youtube-latest">抓最新影片</button>
        <button id="youtube-check">手動檢查新片</button>
      </div>
      <pre id="action-output"></pre>
    </section>

    <section>
      <h2>Memory Mood Board</h2>
      <div class="grid">
        <label class="span-2">選擇已有記憶的頻道<select id="memory-channels"></select></label>
        <label class="span-2">或輸入 Channel ID<input id="memory-channel-id"></label>
      </div>
      <p class="toolbar">
        <button id="load-memory">讀取 Mood Board</button>
        <button id="save-summary">儲存印象卡</button>
        <button id="clear-memory" class="danger">清除這個頻道記憶</button>
      </p>
      <div class="grid">
        <div class="span-2">
          <h3>Bot 對這個頻道的印象卡</h3>
          <textarea id="memory-summary" spellcheck="false"></textarea>
        </div>
        <div class="span-2">
          <h3>近期未壓縮對話</h3>
          <div id="memory-meta" class="note"></div>
          <div id="memory-history" class="history"></div>
        </div>
      </div>
    </section>
  </main>

  <script>
    const el = (id) => document.getElementById(id);
    const tokenInput = el("token");
    const actionOutput = el("action-output");
    tokenInput.value = localStorage.getItem("adminToken") || "";

    function token() {
      return tokenInput.value.trim();
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token()}`,
          ...(options.headers || {})
        }
      });
      const text = await response.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
      if (!response.ok) {
        throw new Error(data.error || data.raw || response.statusText);
      }
      return data;
    }

    function print(data) {
      actionOutput.textContent = JSON.stringify(data, null, 2);
    }

    function fmtUptime(seconds) {
      if (seconds === null || seconds === undefined) return "未知";
      const d = Math.floor(seconds / 86400);
      const h = Math.floor((seconds % 86400) / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = seconds % 60;
      return `${d}天 ${h}小時 ${m}分 ${s}秒`;
    }

    function metric(label, value) {
      return `<div class="metric"><b>${label}</b><span>${value}</span></div>`;
    }

    async function loadStatus() {
      const data = await api("/api/status");
      el("status-grid").innerHTML = [
        metric("Bot", data.bot_user || "未登入"),
        metric("Uptime", fmtUptime(data.uptime_seconds)),
        metric("Latency", `${data.latency_ms} ms`),
        metric("Gemini", data.gemini_model),
        metric("YouTube Loop", data.youtube_loop_running ? "<span class='ok'>運作中</span>" : "<span class='bad'>停止</span>"),
        metric("Cogs", data.loaded_cogs.join(", ")),
        metric("Secrets", `Discord ${data.secrets.discord_token ? "OK" : "NO"} / Gemini ${data.secrets.gemini_api_key ? "OK" : "NO"} / Admin ${data.secrets.admin_token ? "OK" : "NO"}`),
        metric("DB", `history ${data.database_counts.history ?? "-"} / summaries ${data.database_counts.summaries ?? "-"} / youtube ${data.database_counts.youtube_notified ?? "-"}`)
      ].join("");
    }

    async function loadSettings() {
      const data = await api("/api/settings");
      for (const key of ["discord_channel_id", "discord_role_id", "youtube_channel_id", "youtube_check_minutes"]) {
        el(key).value = data[key] ?? "";
      }
    }

    async function saveSettings() {
      const payload = {};
      for (const key of ["discord_channel_id", "discord_role_id", "youtube_channel_id", "youtube_check_minutes"]) {
        payload[key] = el(key).value;
      }
      print(await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }));
    }

    async function loadPersona() {
      const data = await api("/api/persona");
      el("persona").value = data.content || "";
    }

    async function savePersona() {
      print(await api("/api/persona", { method: "PUT", body: JSON.stringify({ content: el("persona").value }) }));
    }

    async function loadPersonas() {
      const data = await api("/api/personas");
      const templates = data.templates || {};
      const options = Object.entries(templates).map(([id, template]) => (
        `<option value="${escapeHtml(id)}">${escapeHtml(template.name || id)}</option>`
      )).join("");
      el("persona-template-select").innerHTML = `<option value="">載入版型</option>${options}`;
      el("persona-assign-template").innerHTML = `<option value="">使用預設 Persona</option>${options}`;
      el("persona-assignments").textContent = JSON.stringify(data.assignments || [], null, 2);
    }

    async function loadDiscordChannels() {
      const data = await api("/api/channels");
      el("persona-channel-select").innerHTML = `<option value="">選擇頻道</option>` + data.channels.map((channel) => (
        `<option value="${channel.id}">${escapeHtml(channel.guild)} / #${escapeHtml(channel.name)} (${channel.id})</option>`
      )).join("");
    }

    async function savePersonaTemplate() {
      const payload = {
        id: el("persona-template-id").value.trim(),
        name: el("persona-template-name").value.trim(),
        content: el("persona").value
      };
      const data = await api("/api/personas/template", { method: "PUT", body: JSON.stringify(payload) });
      el("persona-template-id").value = data.id;
      print(data);
      await loadPersonas();
    }

    async function deletePersonaTemplate() {
      const id = el("persona-template-id").value.trim() || el("persona-template-select").value;
      if (!id) throw new Error("請先選擇或輸入版型 ID");
      if (!confirm(`確定刪除人格版型 ${id}？頻道指派也會一起移除。`)) return;
      print(await api(`/api/personas/template?id=${encodeURIComponent(id)}`, { method: "DELETE" }));
      el("persona-template-id").value = "";
      el("persona-template-name").value = "";
      await loadPersonas();
    }

    async function loadSelectedPersonaTemplate() {
      const id = el("persona-template-select").value;
      if (!id) return;
      const data = await api("/api/personas");
      const template = (data.templates || {})[id];
      if (!template) return;
      el("persona-template-id").value = id;
      el("persona-template-name").value = template.name || id;
      el("persona").value = template.content || "";
    }

    async function assignPersona(clear = false) {
      const channelId = el("persona-channel-id").value.trim();
      if (!channelId) throw new Error("請先輸入 channel ID");
      const templateId = clear ? "" : el("persona-assign-template").value;
      const data = await api("/api/personas/channel", {
        method: "PUT",
        body: JSON.stringify({ channel_id: channelId, template_id: templateId })
      });
      print(data);
      await loadPersonas();
    }

    async function runPersonaLab() {
      const payload = {
        content: el("persona").value,
        message: el("persona-lab-message").value
      };
      const data = await api("/api/persona/lab", { method: "POST", body: JSON.stringify(payload) });
      el("persona-lab-output").textContent = data.reply || "";
    }

    async function reloadCog(extension) {
      print(await api("/api/reload", { method: "POST", body: JSON.stringify({ extension }) }));
      await loadStatus();
      await loadChannels();
      await loadPersonas();
    }

    async function loadLatest() {
      print(await api("/api/youtube/latest"));
    }

    async function checkYoutube() {
      print(await api("/api/youtube/check", { method: "POST", body: "{}" }));
    }

    async function loadChannels() {
      const data = await api("/api/memory/channels");
      el("memory-channels").innerHTML = `<option value="">選擇頻道</option>` + data.channels.map((channel) => {
        const name = channel.name ? ` #${channel.name}` : "";
        return `<option value="${channel.id}">${channel.id}${name}</option>`;
      }).join("");
    }

    function activeMemoryChannelId() {
      return el("memory-channel-id").value.trim() || el("memory-channels").value;
    }

    async function loadMemory() {
      const channelId = activeMemoryChannelId();
      if (!channelId) throw new Error("請先選擇或輸入 channel ID");
      const data = await api(`/api/memory?channel_id=${encodeURIComponent(channelId)}`);
      el("memory-channel-id").value = data.channel_id;
      el("memory-summary").value = data.summary_text || "";
      el("memory-meta").textContent = `${data.channel_name || "未知頻道"} / history ${data.history_count} 筆 / summary ${data.has_summary ? "已建立" : "尚未建立"}`;
      el("memory-history").innerHTML = data.history.map((row) => (
        `<div class="history-row"><small>#${row.id} ${row.timestamp || ""}</small><div>${escapeHtml(row.message)}</div></div>`
      )).join("") || "<p class='note'>目前沒有近期未壓縮對話。</p>";
    }

    async function saveSummary() {
      const channelId = activeMemoryChannelId();
      if (!channelId) throw new Error("請先選擇或輸入 channel ID");
      print(await api("/api/memory/summary", {
        method: "PUT",
        body: JSON.stringify({ channel_id: channelId, summary_text: el("memory-summary").value })
      }));
      await loadChannels();
    }

    async function clearMemory() {
      const channelId = activeMemoryChannelId();
      if (!channelId) throw new Error("請先選擇或輸入 channel ID");
      if (!confirm(`確定清除頻道 ${channelId} 的記憶？`)) return;
      print(await api("/api/memory/clear", { method: "POST", body: JSON.stringify({ channel_id: channelId }) }));
      el("memory-summary").value = "";
      el("memory-history").innerHTML = "";
      await loadChannels();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    async function boot() {
      try {
        await Promise.all([loadStatus(), loadSettings(), loadPersona(), loadPersonas(), loadDiscordChannels(), loadChannels()]);
      } catch (error) {
        print({ error: error.message });
      }
    }

    el("save-token").addEventListener("click", () => {
      localStorage.setItem("adminToken", token());
      boot();
    });
    el("refresh").addEventListener("click", boot);
    el("save-settings").addEventListener("click", () => saveSettings().catch((error) => print({ error: error.message })));
    el("save-persona").addEventListener("click", () => savePersona().catch((error) => print({ error: error.message })));
    el("save-persona-template").addEventListener("click", () => savePersonaTemplate().catch((error) => print({ error: error.message })));
    el("delete-persona-template").addEventListener("click", () => deletePersonaTemplate().catch((error) => print({ error: error.message })));
    el("persona-template-select").addEventListener("change", () => loadSelectedPersonaTemplate().catch((error) => print({ error: error.message })));
    el("persona-channel-select").addEventListener("change", () => { el("persona-channel-id").value = el("persona-channel-select").value; });
    el("assign-persona").addEventListener("click", () => assignPersona(false).catch((error) => print({ error: error.message })));
    el("clear-persona-assignment").addEventListener("click", () => assignPersona(true).catch((error) => print({ error: error.message })));
    el("run-persona-lab").addEventListener("click", () => runPersonaLab().catch((error) => {
      el("persona-lab-output").textContent = JSON.stringify({ error: error.message }, null, 2);
    }));
    el("youtube-latest").addEventListener("click", () => loadLatest().catch((error) => print({ error: error.message })));
    el("youtube-check").addEventListener("click", () => checkYoutube().catch((error) => print({ error: error.message })));
    el("load-memory").addEventListener("click", () => loadMemory().catch((error) => print({ error: error.message })));
    el("save-summary").addEventListener("click", () => saveSummary().catch((error) => print({ error: error.message })));
    el("clear-memory").addEventListener("click", () => clearMemory().catch((error) => print({ error: error.message })));
    el("memory-channels").addEventListener("change", () => { el("memory-channel-id").value = el("memory-channels").value; });
    document.querySelectorAll("[data-reload]").forEach((button) => {
      button.addEventListener("click", () => reloadCog(button.dataset.reload).catch((error) => print({ error: error.message })));
    });

    boot();
  </script>
</body>
</html>
"""


async def setup(bot):
    await bot.add_cog(AdminWeb(bot))
