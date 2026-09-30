import asyncio
import logging
import random
from datetime import datetime, timezone

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

from config import BOT_STATE_DB_PATH, get_youtube_rss_url, load_settings
from services.notification_state import NotificationState
from services.youtube_posts import parse_posts_page

log = logging.getLogger(__name__)


class YouTubeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = BOT_STATE_DB_PATH
        self.state = NotificationState(self.db_path)
        self.session = None
        self.check_lock = asyncio.Lock()
        self.health = {source: {"last_check": "", "last_success": "", "error": "", "failures": 0} for source in ("videos", "posts")}

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=25),
            headers={"User-Agent": "Mozilla/5.0 (compatible; VibeBot/1.0)", "Accept-Language": "en-US,en;q=0.9"},
        )
        self.apply_loop_interval()
        self.check_new_video.start()

    async def cog_unload(self):
        task = self.check_new_video.get_task()
        self.check_new_video.cancel()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        if self.session:
            await self.session.close()

    def current_settings(self):
        return load_settings()

    def current_rss_url(self):
        return get_youtube_rss_url()

    def apply_loop_interval(self):
        self.check_new_video.change_interval(minutes=load_settings()["youtube_check_minutes"])

    async def fetch_bytes(self, url, maximum):
        if self.session is None:
            raise RuntimeError("YouTube client 尚未啟動")
        async with self.session.get(url) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > maximum:
                    raise ValueError("YouTube 回傳資料超過大小限制")
                chunks.append(chunk)
        return b"".join(chunks)

    async def parse_feed(self, settings=None):
        data = await self.fetch_bytes(get_youtube_rss_url(settings), 2 * 1024 * 1024)
        feed = await asyncio.to_thread(feedparser.parse, data)
        if not feed.entries:
            raise ValueError("YouTube RSS 沒有可讀取的影片，通知基準未更新")
        entries = list({entry.id: entry for entry in feed.entries if entry.get("id") and entry.get("link")}.values())
        if not entries:
            raise ValueError("YouTube RSS 影片欄位不完整")
        # RSS usually contains 15 recent videos; sort by publication, not page layout.
        feed.entries = sorted(entries, key=lambda item: item.get("published_parsed") or (0,) * 9, reverse=True)
        return feed

    async def fetch_posts(self, settings=None):
        settings = settings or load_settings()
        channel_id = settings["youtube_channel_id"]
        data = await self.fetch_bytes(f"https://www.youtube.com/channel/{channel_id}/posts?hl=en", 6 * 1024 * 1024)
        return await asyncio.to_thread(parse_posts_page, data.decode("utf-8"), channel_id)

    def post_embed(self, post):
        content = discord.utils.escape_mentions(post.text) or "（圖片、投票或影片貼文，請開啟原文）"
        embed = discord.Embed(title="📣 YouTube 新貼文", url=post.link, description=content[:1800], color=0xEF4444)
        if post.author:
            embed.set_author(name=post.author[:256])
        if post.published:
            embed.set_footer(text=post.published[:200])
        if post.image_url.startswith("https://"):
            embed.set_image(url=post.image_url)
        return embed

    async def notification_channel(self, settings):
        channel = self.bot.get_channel(settings["discord_channel_id"])
        if channel is None:
            channel = await self.bot.fetch_channel(settings["discord_channel_id"])
        if not hasattr(channel, "send"):
            raise ValueError("通知目標不是可傳送訊息的頻道")
        return channel

    async def send_notification(self, source, item, settings):
        channel = await self.notification_channel(settings)
        role_id = settings["discord_role_id"]
        mention = f"<@&{role_id}>" if role_id else ""
        mentions = discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(role_id)] if role_id else [], replied_user=False)
        if source == "posts":
            await channel.send(content=mention or None, embed=self.post_embed(item), allowed_mentions=mentions)
        else:
            title = discord.utils.escape_markdown(discord.utils.escape_mentions(item.get("title", "新影片")))[:600]
            await channel.send(f"📢 {mention} ✌🥺✌ 新影片來了！\n**{title}**\n{item.link}", allowed_mentions=mentions)

    async def check_source(self, source, settings):
        health = self.health[source]
        health["last_check"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            items = (await self.parse_feed(settings)).entries if source == "videos" else await self.fetch_posts(settings)
            # Do not deliver a stale in-flight fetch to a newly configured destination.
            active = load_settings()
            watched = ("youtube_channel_id", "discord_channel_id", "discord_role_id", "youtube_posts_enabled")
            if any(active[key] != settings[key] for key in watched):
                return {"sent": 0, "note": "設定在檢查期間已變更，下次重新檢查"}
            pending = self.state.prepare(settings["youtube_channel_id"], source, items)
            sent = 0
            for item in reversed(pending):
                active = load_settings()
                if any(active[key] != settings[key] for key in watched):
                    return {"sent": sent, "note": "設定已變更，停止本次傳送"}
                await self.send_notification(source, item, settings)
                self.state.delivered(settings["youtube_channel_id"], source, item)
                sent += 1
            health.update(last_success=health["last_check"], error="", failures=0)
            return {"sent": sent, "visible": len(items)}
        except Exception as error:
            health["failures"] += 1
            if isinstance(error, ValueError):
                health["error"] = str(error)[:300]
            elif isinstance(error, (asyncio.TimeoutError, TimeoutError)):
                health["error"] = "來源連線逾時，下次巡邏會重試"
            elif isinstance(error, discord.Forbidden):
                health["error"] = "Discord 通知頻道權限不足"
            else:
                health["error"] = f"{type(error).__name__}：檢查或傳送失敗，詳細資訊見伺服器日誌"
            log.exception("YouTube %s 檢查失敗", source)
            return {"error": health["error"]}

    async def check_all_once(self):
        async with self.check_lock:
            settings = load_settings()
            sources = ["videos"] + (["posts"] if settings["youtube_posts_enabled"] else [])
            results = await asyncio.gather(*(self.check_source(source, settings) for source in sources))
            return dict(zip(sources, results))

    async def check_latest_video_once(self):
        # Compatibility entry point for the former admin API.
        async with self.check_lock:
            return await self.check_source("videos", load_settings())

    @tasks.loop(minutes=5)
    async def check_new_video(self):
        try:
            self.apply_loop_interval()
            await self.check_all_once()
        except Exception:
            log.exception("巡邏發生錯誤；下一輪繼續")

    @check_new_video.before_loop
    async def before_polling(self):
        await self.bot.wait_until_ready()

    async def send_video_command(self, interaction, random_choice=False):
        await interaction.response.defer(thinking=True)
        try:
            entries = (await self.parse_feed()).entries
            item = random.choice(entries) if random_choice else entries[0]
            title = discord.utils.escape_markdown(discord.utils.escape_mentions(item.get("title", "影片")))[:600]
            await interaction.followup.send(f"✌🥺✌ **{title}**\n{item.link}", allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            log.exception("查詢影片失敗")
            await interaction.followup.send("暫時無法取得影片，請稍後再試。")

    @app_commands.command(name="最新影片", description="查看追蹤頻道的最新影片。")
    async def latest_video(self, interaction: discord.Interaction):
        await self.send_video_command(interaction)

    @app_commands.command(name="隨意看", description="從 RSS 最近影片隨機抽一支。")
    async def random_video(self, interaction: discord.Interaction):
        await self.send_video_command(interaction, random_choice=True)

    @app_commands.command(name="最新貼文", description="查看追蹤 YouTube 頻道的最新公開貼文。")
    async def latest_post(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            posts = await self.fetch_posts()
            if posts:
                await interaction.followup.send(embed=self.post_embed(posts[0]), allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.followup.send("這個頻道目前沒有公開貼文。")
        except Exception:
            log.exception("查詢貼文失敗")
            await interaction.followup.send("暫時無法取得公開貼文。可能是 YouTube 存取限制或頁面格式變更，請稍後再試。")


async def setup(bot):
    await bot.add_cog(YouTubeTracker(bot))
