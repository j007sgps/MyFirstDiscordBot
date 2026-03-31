import discord
from discord.ext import tasks
import feedparser
import asyncio
import os
import random
from dotenv import load_dotenv

# 載入 .env 檔案中隱藏的密碼
load_dotenv()

# === 在這裡填入你的資料 ===
# 改為從 .env 環境變數檔案讀取 Token，避免密碼寫死在程式碼裡
TOKEN = os.getenv('DISCORD_TOKEN')

# 想要發送通知的 Discord 頻道 ID (數字)
DISCORD_CHANNEL_ID = 1485639623899218021  

# YouTube 頻道 ID (例如老高是 UCMUnInmOkrWN4gof9KlhNmQ)
YOUTUBE_CHANNEL_ID = 'UCwUvX4_nrbYGhlRxqJIB3JA'

# [可選] 如果我們想要標記某個身分組，填入該身分組的 ID (如果是標記所有人，可以留空白)
# 注意：這是一串純數字，例如 123456789012345678
DISCORD_ROLE_ID = 1485643846845993061
# ========================

# 建立機器人
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# YouTube 的 RSS 訂閱網址格式
YOUTUBE_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"

# 用來記錄最新一部影片的 ID，避免重複發送
last_video_id = ""

@client.event
async def on_ready():
    print(f'機器人已上線！目前登入身份：{client.user}')
    
    # 找到我們要發送通知的頻道
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel:
        # 發送上線成功通知到 Discord 內
        await channel.send("👋 とうも、限界社畜ぅ！！！きぜつちゃんだよ！！（bot啟動測試）")
        
    # 機器人準備好後，開始啟動定時檢查迴圈
    check_new_video.start()

# 當有訊息時會觸發這個事件
@client.event
async def on_message(message):
    # 避免機器人自己回應自己，造成無限迴圈
    if message.author == client.user:
        return

    # 指令一：顯示最新影片
    if message.content == '!最新影片':
        # 為了讓使用者立刻知道機器人有收到，我們可以顯示正在輸入的狀態
        async with message.channel.typing():
            feed = feedparser.parse(YOUTUBE_RSS_URL)
            if len(feed.entries) > 0:
                latest = feed.entries[0]
                await message.reply(f"📺 這是最新發布的影片：**{latest.title}**\n點此觀看：{latest.link}")
            else:
                await message.reply("目前在這個頻道沒有找到影片喔！")

    # 指令二：顯示隨機影片
    if message.content == '!隨意看':
        async with message.channel.typing():
            feed = feedparser.parse(YOUTUBE_RSS_URL)
            if len(feed.entries) > 0:
                # RSS 預設通常包含最近的 15 支影片清單，我們從裡面隨機挑一支
                random_video = random.choice(feed.entries)
                await message.reply(f"🎲 為你隨機抽取了一部影片：**{random_video.title}**\n碰碰運氣看看這部吧：{random_video.link}")
            else:
                await message.reply("目前在這個頻道沒有找到影片喔！")

    # 指令三：求救選單
    if message.content == '!help' or message.content == '!說明':
        help_text = (
            "🤖 **你可以對我下達這些指令：**\n\n"
            "▶️ `!最新影片`：讓我幫你找出頻道最新發布的作品！\n"
            "▶️ `!隨意看`：不知道看什麼？讓我幫你隨機抽一部影片看看～\n"
            "▶️ `!help` 或 `!說明`：呼叫這個說明選單。\n\n"
            "💡 *除此之外，只要該頻道有發新影片，我也會第一時間通知大家喔！*"
        )
        await message.reply(help_text)

# 定義一個定時任務，每 5 分鐘執行一次
@tasks.loop(minutes=5)
async def check_new_video():
    global last_video_id
    
    # 讀取 YouTube 頻道的 RSS
    feed = feedparser.parse(YOUTUBE_RSS_URL)
    
    # 如果這個頻道有影片
    if len(feed.entries) > 0:
        # 取得最新的一部影片資訊
        latest_video = feed.entries[0]
        video_id = latest_video.id
        
        # 檢查是不是有新影片（跟上一次檢查的ID不一樣）
        if last_video_id != "" and video_id != last_video_id:
            # 找到你想發佈的頻道
            channel = client.get_channel(DISCORD_CHANNEL_ID)
            
            # 組合出要發送的訊息
            video_title = latest_video.title
            video_link = latest_video.link
            
            # 如果有設定身分組 ID，就在最前面加上標記
            if DISCORD_ROLE_ID != "":
                message = f"📢 <@&{DISCORD_ROLE_ID}> ！有新影片啦：**{video_title}**\n趕快來看：{video_link}"
            else:
                message = f"📢 大家注意！有新影片啦：**{video_title}**\n趕快來看：{video_link}"
            
            # 傳送訊息到 Discord
            await channel.send(message)
            print(f"已發送通知：{video_title}")
            
        # 更新最後一次看到的影片 ID
        last_video_id = video_id
        

# 啟動機器人
client.run(TOKEN)
