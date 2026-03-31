import discord
from discord.ext import commands, tasks
import feedparser
import random
from config import YOUTUBE_RSS_URL, DISCORD_CHANNEL_ID, DISCORD_ROLE_ID

class YouTubeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_video_id = ""
        # 啟動定時任務
        self.check_new_video.start()

    def cog_unload(self):
        # 模組卸載時停止計時器
        self.check_new_video.cancel()

    # 指令：!最新影片
    @commands.command(name="最新影片")
    async def latest_video(self, ctx):
        async with ctx.typing():
            feed = feedparser.parse(YOUTUBE_RSS_URL)
            if len(feed.entries) > 0:
                latest = feed.entries[0]
                await ctx.reply(f"✌🥺✌ 就算現在是大半夜，還是只能看這部了吧！！\n為你送上最新鮮的背德美食：**{latest.title}**\n大腦破壞就在這裡：{latest.link}")
            else:
                await ctx.reply("目前在這個頻道沒有找到影片喔！")

    # 指令：!隨意看
    @commands.command(name="隨意看")
    async def random_video(self, ctx):
        async with ctx.typing():
            feed = feedparser.parse(YOUTUBE_RSS_URL)
            if len(feed.entries) > 0:
                random_v = random.choice(feed.entries)
                await ctx.reply(f"✌🥺✌ 深夜突然好餓...那只能點開這部**笨蛋等級美味**的影片了吧！\n🎲 為你隨機送上一場罪惡之宴：**{random_v.title}**\n一起讓理智融化吧：{random_v.link}")
            else:
                await ctx.reply("目前在這個頻道沒有找到影片喔！")

    # 定時任務：每 5 分鐘檢查一次 YouTube
    @tasks.loop(minutes=5)
    async def check_new_video(self):
        await self.bot.wait_until_ready()
        feed = feedparser.parse(YOUTUBE_RSS_URL)
        if len(feed.entries) > 0:
            latest = feed.entries[0]
            if self.last_video_id != "" and latest.id != self.last_video_id:
                channel = self.bot.get_channel(DISCORD_CHANNEL_ID)
                if channel:
                    role_mention = f"<@&{DISCORD_ROLE_ID}> " if DISCORD_ROLE_ID else ""
                    msg = f"📢 {role_mention}✌🥺✌ 真的假的？？竟然有新影片！！\n深夜的背德美食來了... 理智要融化啦～！\n快來看這破壞大腦的影片：**{latest.title}**\n{latest.link}"
                    await channel.send(msg)
            self.last_video_id = latest.id

# 必須存在的 setup 函式，用來把這個 Cog 註冊進主程式中
async def setup(bot):
    await bot.add_cog(YouTubeTracker(bot))
