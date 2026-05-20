import discord
from discord import app_commands
from discord.ext import commands
import os
import sqlite3
from datetime import datetime, timezone
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

class VibeBot(commands.Bot):
    async def setup_hook(self):
        # [新兵訓練] 讀取並裝上不同的專屬能力齒輪 (Cogs)
        await self.load_extension("cogs.youtube")
        await self.load_extension("cogs.ai_chat")
        synced_commands = await self.tree.sync()
        print(f"Slash commands 已同步：{len(synced_commands)} 個")

# 4. 建立機器人：保留 commands.Bot 作為 Cog 容器，主要互動改用 Slash Commands
bot = VibeBot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
STARTED_AT = datetime.now(timezone.utc)

def format_uptime(delta):
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小時")
    if minutes:
        parts.append(f"{minutes}分")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return " ".join(parts)

def count_sqlite_rows(db_path, table_name):
    if not os.path.exists(db_path):
        return "尚未建立"

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            return str(cursor.fetchone()[0])
    except sqlite3.Error:
        return "讀取失敗"

# ============== 主程式事件 ==============

@bot.event
async def on_ready():
    # 當機器人登入完成後會執行下面的事情：
    print(f'機器人已上線！目前登入身份：{bot.user}')
    print("模組全部載入完畢，準備接受背德美食的指令！✌🥺✌")

# ================= 隱藏管理員指令 =================
@bot.tree.command(name="reload", description="重新載入指定 Cog 模組。只有 bot owner 可以使用。")
@app_commands.describe(extension="要重新載入的 cog 名稱，例如 ai_chat 或 youtube")
async def reload(interaction: discord.Interaction, extension: str):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("這是 owner-only 指令。哼，權限不夠就不要亂摸開關。", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        await bot.reload_extension(f"cogs.{extension}")
        synced_commands = await bot.tree.sync()
        await interaction.followup.send(
            f"✅ 模組 `{extension}` 已經重新載入完成，Slash commands 也同步了 {len(synced_commands)} 個！✌🥺✌",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ 重載模組失敗 (大腦破壞)：\n```\n{e}\n```", ephemeral=True)

def build_status_text():
    now = datetime.now(timezone.utc)
    ai_cog = bot.get_cog("AIChat")
    youtube_cog = bot.get_cog("YouTubeTracker")
    youtube_loop = "運作中" if youtube_cog and youtube_cog.check_new_video.is_running() else "未運作"
    loaded_cogs = ", ".join(sorted(bot.extensions.keys())) or "無"
    latency_ms = round(bot.latency * 1000)
    gemini_status = "已設定" if os.getenv("GEMINI_API_KEY") else "未設定"
    discord_token_status = "已設定" if TOKEN else "未設定"

    gemini_model = "未載入"
    if ai_cog and hasattr(ai_cog, "model"):
        try:
            model_name = getattr(ai_cog.model, "model_name", "") or getattr(ai_cog.model, "_model_name", "")
            if model_name:
                if model_name.startswith("models/"):
                    model_name = model_name[7:]
                gemini_model = model_name
            else:
                gemini_model = "無法取得模型名稱"
        except Exception as e:
            gemini_model = f"讀取失敗 ({e})"

    return (
        "✌🥺✌ **Bot 狀態報告**\n"
        f"上線時間：{format_uptime(now - STARTED_AT)}\n"
        f"Discord 延遲：{latency_ms} ms\n"
        f"Discord Token：{discord_token_status}\n"
        f"Gemini API Key：{gemini_status}\n"
        f"Gemini 模型：{gemini_model}\n"
        f"已載入模組：{loaded_cogs}\n"
        f"AI Chat Cog：{'已載入' if ai_cog else '未載入'}\n"
        f"YouTube Cog：{'已載入' if youtube_cog else '未載入'}\n"
        f"YouTube 巡邏任務：{youtube_loop}\n"
        f"AI 近期記憶筆數：{count_sqlite_rows('chat_history.db', 'history')}\n"
        f"AI 長期摘要頻道數：{count_sqlite_rows('chat_history.db', 'summaries')}\n"
        f"YouTube 已通知影片數：{count_sqlite_rows('bot_state.db', 'youtube_notified')}"
    )

@bot.tree.command(name="status", description="查看 bot 延遲、上線時間、模組與資料庫狀態。")
async def status(interaction: discord.Interaction):
    await interaction.response.send_message(build_status_text())

@bot.tree.command(name="狀態", description="查看 bot 延遲、上線時間、模組與資料庫狀態。")
async def status_zh(interaction: discord.Interaction):
    await interaction.response.send_message(build_status_text())

# 這裡就是機器的啟動台！
if __name__ == "__main__":
    bot.run(TOKEN)
