import hmac
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web
from discord.ext import commands

from config import load_settings, save_settings


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
        app.router.add_get("/api/persona", self.handle_get_persona)
        app.router.add_put("/api/persona", self.handle_put_persona)
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

    async def handle_get_persona(self, request):
        if not PERSONA_PATH.exists():
            return self.json_response({"content": ""})
        return self.json_response({"content": PERSONA_PATH.read_text(encoding="utf-8")})

    async def handle_put_persona(self, request):
        payload = await request.json()
        content = str(payload.get("content", ""))
        PERSONA_PATH.write_text(content, encoding="utf-8")
        return self.json_response({"ok": True, "bytes": len(content.encode("utf-8"))})

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
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1d2430;
      --muted: #657083;
      --line: #d7dce3;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --danger: #b42318;
      --warn: #925400;
      --mono: "Cascadia Mono", Consolas, monospace;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 { margin: 0; font-size: 22px; }
    h2 { margin: 0 0 12px; font-size: 18px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    main {
      width: min(1180px, calc(100% - 32px));
      margin: 20px auto 48px;
      display: grid;
      gap: 16px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
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
      border-radius: 8px;
      padding: 12px;
      min-height: 82px;
      background: #fbfcfd;
    }
    .metric b { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; }
    .metric span { font-size: 20px; font-weight: 700; overflow-wrap: anywhere; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 13px; }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 9px 10px;
      font: inherit;
    }
    textarea { min-height: 220px; resize: vertical; font-family: var(--mono); font-size: 13px; }
    button {
      border: 1px solid var(--accent-dark);
      background: var(--accent);
      color: #fff;
      border-radius: 6px;
      padding: 9px 12px;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary { background: #fff; color: var(--accent-dark); }
    button.danger { background: var(--danger); border-color: var(--danger); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    pre {
      overflow: auto;
      background: #111827;
      color: #e5e7eb;
      border-radius: 8px;
      padding: 12px;
      min-height: 80px;
      white-space: pre-wrap;
    }
    .note { color: var(--muted); font-size: 13px; }
    .ok { color: var(--accent-dark); font-weight: 700; }
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
      border-radius: 8px;
      padding: 10px;
      background: #fbfcfd;
    }
    .history-row small { color: var(--muted); display: block; margin-bottom: 4px; }
    @media (max-width: 880px) {
      header { align-items: flex-start; }
      .grid { grid-template-columns: 1fr; }
      .span-2, .span-4 { grid-column: span 1; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Bot Admin</h1>
      <div class="note">私用管理面板，API token 只存在瀏覽器 localStorage。</div>
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
      <textarea id="persona" spellcheck="false"></textarea>
      <p class="toolbar"><button id="save-persona">儲存 Persona</button><button class="secondary" data-reload="ai_chat">Reload AI Chat</button></p>
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

    async function reloadCog(extension) {
      print(await api("/api/reload", { method: "POST", body: JSON.stringify({ extension }) }));
      await loadStatus();
      await loadChannels();
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
        await Promise.all([loadStatus(), loadSettings(), loadPersona(), loadChannels()]);
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
