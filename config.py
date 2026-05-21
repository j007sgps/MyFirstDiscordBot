import json
from pathlib import Path

# ========================
# 專案的獨立設定檔 (集中管理常數)
# ========================

SETTINGS_PATH = Path("settings.json")

DEFAULT_SETTINGS = {
    "discord_channel_id": 1485639623899218021,
    "youtube_channel_id": "UCwUvX4_nrbYGhlRxqJIB3JA",
    "discord_role_id": 1485643846845993061,
    "youtube_check_minutes": 5,
}


def load_settings():
    settings = DEFAULT_SETTINGS.copy()
    if not SETTINGS_PATH.exists():
        return settings

    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return settings

    if isinstance(loaded, dict):
        settings.update({key: loaded.get(key, value) for key, value in DEFAULT_SETTINGS.items()})
    return settings


def save_settings(settings):
    merged = DEFAULT_SETTINGS.copy()
    merged.update({key: settings.get(key, value) for key, value in DEFAULT_SETTINGS.items()})

    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return merged


def get_youtube_rss_url(settings=None):
    active_settings = settings or load_settings()
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={active_settings['youtube_channel_id']}"


_settings = load_settings()

# 舊名稱保留，讓還沒改到的程式碼也可以正常 import。
DISCORD_CHANNEL_ID = int(_settings["discord_channel_id"])
YOUTUBE_CHANNEL_ID = str(_settings["youtube_channel_id"])
DISCORD_ROLE_ID = int(_settings["discord_role_id"]) if _settings["discord_role_id"] else 0
YOUTUBE_CHECK_MINUTES = float(_settings["youtube_check_minutes"])
YOUTUBE_RSS_URL = get_youtube_rss_url(_settings)
