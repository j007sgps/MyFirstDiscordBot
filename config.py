"""Validated configuration, anchored to the project directory."""
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
PERSONAS_PATH = BASE_DIR / "personas.json"
PERSONA_PATH = BASE_DIR / "shachiku.md"
CHAT_DB_PATH = BASE_DIR / "chat_history.db"
BOT_STATE_DB_PATH = BASE_DIR / "bot_state.db"
MODEL_CHOICES = ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash")
DEFAULT_SETTINGS = {
    "discord_channel_id": 1485639623899218021,
    "youtube_channel_id": "UCwUvX4_nrbYGhlRxqJIB3JA",
    "discord_role_id": 1485643846845993061,
    "youtube_check_minutes": 5,
    "youtube_posts_enabled": True,
    "gemini_model": MODEL_CHOICES[0],
    "ai_search_enabled": False,
    "ai_max_output_tokens": 2048,
}
log = logging.getLogger(__name__)


def atomic_write_text(path, text):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_settings(settings):
    merged = DEFAULT_SETTINGS | {key: settings[key] for key in DEFAULT_SETTINGS if key in settings}
    for key in ("discord_channel_id", "discord_role_id", "ai_max_output_tokens"):
        value = merged[key]
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError(f"{key} 必須是整數")
        merged[key] = int(value)
    if not 0 < merged["discord_channel_id"] < 2**64 or not 0 <= merged["discord_role_id"] < 2**64:
        raise ValueError("Discord ID 無效")
    if not 256 <= merged["ai_max_output_tokens"] <= 8192:
        raise ValueError("回覆 token 上限必須介於 256～8192")
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", str(merged["youtube_channel_id"])):
        raise ValueError("YouTube 頻道 ID 必須是 UC 開頭的 24 字元 ID")
    if isinstance(merged["youtube_check_minutes"], bool):
        raise ValueError("檢查間隔必須是數字")
    minutes = float(merged["youtube_check_minutes"])
    if not math.isfinite(minutes) or not 1 <= minutes <= 1440:
        raise ValueError("檢查間隔必須介於 1～1440 分鐘")
    merged["youtube_check_minutes"] = minutes
    if merged["gemini_model"] not in MODEL_CHOICES:
        raise ValueError("請選擇支援的 Gemini 模型")
    for key in ("youtube_posts_enabled", "ai_search_enabled"):
        if not isinstance(merged[key], bool):
            raise ValueError(f"{key} 必須是布林值")
    return merged


def load_settings():
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("設定必須是 JSON object")
        return validate_settings(loaded)
    except (OSError, ValueError, TypeError):
        log.exception("settings.json 無法讀取；停止使用該設定，原檔未覆寫")
        raise ValueError("settings.json 無法讀取，請修復或還原，避免通知發往錯誤目標") from None


def save_settings(settings):
    merged = validate_settings(load_settings() | settings)
    atomic_write_text(SETTINGS_PATH, json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    return merged


def get_youtube_rss_url(settings=None):
    active = settings if settings is not None else load_settings()
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={active['youtube_channel_id']}"


def load_persona_store():
    if not PERSONAS_PATH.exists():
        return {"templates": {}, "channel_personas": {}}
    try:
        loaded = json.loads(PERSONAS_PATH.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("templates", {}), dict) or not isinstance(loaded.get("channel_personas", {}), dict):
            raise ValueError("人格資料格式無效")
        templates = loaded.get("templates", {})
        if any(not isinstance(value, dict) or not isinstance(value.get("content", ""), str) for value in templates.values()):
            raise ValueError("人格版型格式無效")
        return {"templates": templates, "channel_personas": {str(k): str(v) for k, v in loaded.get("channel_personas", {}).items()}}
    except (OSError, ValueError, TypeError):
        raise ValueError("personas.json 無法讀取，請先修復或還原檔案") from None


def save_persona_store(store):
    normalized = {"templates": store.get("templates", {}), "channel_personas": {str(k): str(v) for k, v in store.get("channel_personas", {}).items() if v}}
    atomic_write_text(PERSONAS_PATH, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return normalized


# 舊匯入名稱保留；執行時功能應使用 load_settings()。
_settings = load_settings()
DISCORD_CHANNEL_ID = _settings["discord_channel_id"]
YOUTUBE_CHANNEL_ID = _settings["youtube_channel_id"]
DISCORD_ROLE_ID = _settings["discord_role_id"]
YOUTUBE_CHECK_MINUTES = _settings["youtube_check_minutes"]
YOUTUBE_RSS_URL = get_youtube_rss_url(_settings)
