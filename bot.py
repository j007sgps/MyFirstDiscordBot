import discord
from discord.ext import tasks
import feedparser
import asyncio
import os
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
            message = f"📢 大家注意！有新影片啦：**{video_title}**\n趕快來看：{video_link}"
            
            # 傳送訊息到 Discord
            await channel.send(message)
            print(f"已發送通知：{video_title}")
            
        # 更新最後一次看到的影片 ID
        last_video_id = video_id
        

# 啟動機器人
client.run(TOKEN)
