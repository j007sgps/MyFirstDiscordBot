import discord
from discord.ext import commands
import os
import google.generativeai as genai
from dotenv import load_dotenv

# ============== 初始化設定 ==============
# 1. 載入 .env 檔案中隱藏的密碼
load_dotenv()

# 2. 從環境變數讀取密碼（必須在 load_dotenv 之後執行！）
TOKEN = os.getenv('DISCORD_TOKEN')
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

# 3. 設定 Discord 意圖 (Intents)
intents = discord.Intents.default()
intents.message_content = True

# 4. 建立機器人：將原本的 discord.Client 升級為支援 Cogs 模組化的 commands.Bot
# command_prefix='!' 代表所有使用指令的開頭，help_command=None 讓我們可以直接自訂 !help
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ============== 主程式事件 ==============

@bot.event
async def on_ready():
    # 當機器人登入完成後會執行下面的事情：
    print(f'機器人已上線！目前登入身份：{bot.user}')
    
    # [新兵訓練] 讀取並裝上不同的專屬能力齒輪 (Cogs)
    await bot.load_extension("cogs.youtube")
    await bot.load_extension("cogs.ai_chat")
    
    print("模組全部載入完畢，準備接受背德美食的指令！✌🥺✌")

# ================= 隱藏管理員指令 =================
@bot.command(name="reload")
@commands.is_owner() # 確保只有機器人擁有者(你本人)可以執行
async def reload(ctx, extension: str):
    """(隱藏指令) 在不關機的情況下重新載入 Cog 模組"""
    try:
        await bot.reload_extension(f"cogs.{extension}")
        await ctx.send(f"✅ 模組 `{extension}` 已經重新載入完成！✌🥺✌")
    except Exception as e:
        await ctx.send(f"❌ 重載模組失敗 (大腦破壞)：\n```\n{e}\n```")

# 這裡就是機器的啟動台！
if __name__ == "__main__":
    bot.run(TOKEN)
