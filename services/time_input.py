"""Human time input. Naive calendar times use Japan's UTC+09:00."""
import re
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def parse_time(value, *, now=None, max_days=30):
    now = now or datetime.now(timezone.utc)
    value = value.strip().lower()
    compact = re.sub(r"\s+", "", value)
    units = {"秒": 1, "秒鐘": 1, "s": 1, "分": 60, "分鐘": 60, "m": 60,
             "小時": 3600, "時": 3600, "h": 3600, "天": 86400, "日": 86400, "d": 86400}
    parts = list(re.finditer(r"(\d+)(秒鐘|分鐘|小時|秒|分|時|天|日|s|m|h|d)", compact))
    if parts and "".join(part.group() for part in parts) == compact:
        seconds = sum(int(part[1]) * units[part[2]] for part in parts)
        if seconds > max_days * 86400:
            raise ValueError(f"時間最多可排到 {max_days} 天後")
        target = now + timedelta(seconds=seconds)
    else:
        relative = re.fullmatch(r"(今天|明天)\s*(\d{1,2}:\d{2})", value)
        try:
            if relative:
                local = now.astimezone(JST) + timedelta(days=1 if relative[1] == "明天" else 0)
                hour, minute = map(int, relative[2].split(":"))
                target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                target = datetime.fromisoformat(value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=JST)
        except (ValueError, OverflowError):
            raise ValueError("時間請填 30分鐘、1小時30分、明天 21:00，或 YYYY-MM-DD HH:MM（日本時間）") from None
    delay = (target - now).total_seconds()
    if delay < 60:
        raise ValueError("時間至少需在 1 分鐘後；已過去的時間不會自動改到明天")
    if delay > max_days * 86400:
        raise ValueError(f"時間最多可排到 {max_days} 天後")
    return target.astimezone(timezone.utc).timestamp()


def poll_options(value):
    options = [part.strip() for part in re.split(r"[,，、\n]", value)]
    if not 2 <= len(options) <= 10 or any(not option or len(option) > 60 for option in options):
        raise ValueError("請提供 2～10 個非空選項，以逗號分隔，每項最多 60 字")
    if len(set(options)) != len(options):
        raise ValueError("投票選項不能重複")
    return options
