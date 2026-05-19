import discord
from discord.ext import commands
import google.generativeai as genai
import sqlite3

SAFE_MESSAGE_LIMIT = 1900

class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 記憶系統：改用 SQLite 達成永久記憶！
        self.db_path = "chat_history.db"
        self.init_db()
        
        # 讀取人設檔案 shachiku.md，把它的內容變成字串交給 AI
        try:
            with open("shachiku.md", "r", encoding="utf-8") as f:
                system_instruction = f.read()
        except FileNotFoundError:
            system_instruction = "你是一隻限界社畜，喜歡在深夜大吃特吃背德美食。"

        # 建立 Gemini AI 模型並賦予人格！
        self.model = genai.GenerativeModel(
            model_name="gemini-3-flash-preview", # 使用更輕巧、快速的 flash 版本！
            system_instruction=system_instruction
        )

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER,
                    message TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 新增儲存摘要的表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS summaries (
                    channel_id INTEGER PRIMARY KEY,
                    summary_text TEXT
                )
            ''')
            conn.commit()

    def add_memory(self, channel_id, message):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO history (channel_id, message) VALUES (?, ?)', (channel_id, message))
            conn.commit()

    def get_memory_with_ids(self, channel_id, limit=20):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, message FROM history 
                WHERE channel_id = ? 
                ORDER BY id DESC LIMIT ?
            ''', (channel_id, limit))
            rows = cursor.fetchall()
            return list(reversed(rows))

    def get_summary(self, channel_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT summary_text FROM summaries WHERE channel_id = ?', (channel_id,))
            row = cursor.fetchone()
            return row[0] if row else ""

    def save_summary(self, channel_id, summary_text):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                REPLACE INTO summaries (channel_id, summary_text) 
                VALUES (?, ?)
            ''', (channel_id, summary_text))
            conn.commit()

    def clear_history(self, channel_id, up_to_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM history WHERE channel_id = ? AND id <= ?', (channel_id, up_to_id))
            conn.commit()

    def clear_channel_memory(self, channel_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM history WHERE channel_id = ?', (channel_id,))
            cursor.execute('DELETE FROM summaries WHERE channel_id = ?', (channel_id,))
            conn.commit()

    def split_message(self, text, limit=SAFE_MESSAGE_LIMIT):
        if not text:
            return [""]

        chunks = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at == -1:
                split_at = remaining.rfind(" ", 0, limit)
            if split_at == -1:
                split_at = limit

            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()

        if remaining:
            chunks.append(remaining)
        return chunks

    async def send_chunked_reply(self, message, text):
        chunks = self.split_message(text)
        await message.reply(chunks[0])
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

    async def send_chunked_ctx_reply(self, ctx, text):
        chunks = self.split_message(text)
        await ctx.reply(chunks[0])
        for chunk in chunks[1:]:
            await ctx.send(chunk)

    # 取代原本在 bot.py 裡的 !help
    @commands.command(name="help", aliases=["說明"])
    async def custom_help(self, ctx):
        help_text = (
            "✌🥺✌ **為了健康生活，深夜就該吃背德美食！！** \n\n"
            "🍟 **你可以對我下達這些罪惡的指令：**\n"
            "▶️ `!最新影片`：讓我端上剛出爐、熱量爆表的最新發布影片！\n"
            "▶️ `!隨意看`：深夜不知道看什麼？讓我隨機為你挑一場破壞大腦的罪惡之宴～\n"
            "▶️ `!memory` / `!記憶`：查看這個頻道的 AI 記憶摘要與近期對話。\n"
            "▶️ `!forget` / `!忘記`：清掉這個頻道的 AI 記憶。只有 owner 可以用。\n"
            "▶️ `!status` / `!狀態`：查看 bot 延遲、上線時間、模組與資料庫狀態。\n"
            "▶️ `!help` 或 `!說明`：呼叫這個說明選單。\n\n"
            "🤖 **[新功能！] 靈魂注入的 AI 聊天**：直接在群組裡標記我 (@限界社畜！！！)，然後跟我講話吧！\n\n"
            "💡 *當然啦，只要有新影片發布，我也會第一時間通知做個吃貨傢伙！是真的假的？？？*"
        )
        await self.send_chunked_ctx_reply(ctx, help_text)

    @commands.command(name="memory", aliases=["記憶"])
    @commands.is_owner()
    async def show_memory(self, ctx):
        channel_id = ctx.channel.id
        summary = self.get_summary(channel_id)
        history_rows = self.get_memory_with_ids(channel_id, limit=10)

        if not summary and not history_rows:
            await ctx.reply("這個頻道目前沒有記憶。哼，乾淨得有點可疑。")
            return

        parts = [f"**頻道記憶：{ctx.channel.name}**"]
        if summary:
            parts.append(f"**長期摘要**\n{summary}")
        else:
            parts.append("**長期摘要**\n目前沒有壓縮摘要。")

        if history_rows:
            history_text = "\n".join([f"{row_id}. {message}" for row_id, message in history_rows])
            parts.append(f"**近期未壓縮對話**\n{history_text}")
        else:
            parts.append("**近期未壓縮對話**\n目前沒有。")

        await self.send_chunked_ctx_reply(ctx, "\n\n".join(parts))

    @commands.command(name="forget", aliases=["忘記"])
    @commands.is_owner()
    async def forget_memory(self, ctx):
        self.clear_channel_memory(ctx.channel.id)
        await ctx.reply("這個頻道的 AI 記憶已經清掉了。不是我想忘，是你叫我忘的喔。")

    # 當有人發言時
    @commands.Cog.listener()
    async def on_message(self, message):
        # 如果是機器人自己發的不要理，避免無限迴圈！
        if message.author == self.bot.user:
            return

        # 攔截這則訊息：只有標記到我 (機器人本身) 一個人，且沒有標記 @everyone 或其他身份組時才反應
        if self.bot.user in message.mentions and len(message.mentions) == 1 and not message.mention_everyone and not message.role_mentions:
            # 把 "@機器人" 的字眼過濾掉，只留下真正的問題
            user_msg = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
            
            # 取得頻道 ID 與發言者名稱，準備用來做記憶
            channel_id = message.channel.id
            current_user = message.author.display_name
            
            # 檢查是否有文字或圖片附件
            if user_msg or message.attachments:
                # 顯示 "機器人正在輸入..." 的狀態
                async with message.channel.typing():
                    try:
                        # 準備要傳遞給 Gemini 的內容清單
                        contents = []
                        prompt_text = ""
                        
                        # 0. 讀取過去的摘要 (現在是人物誌)
                        summary = self.get_summary(channel_id)
                        if summary:
                            prompt_text += f"【群組成員人物誌（你的長期記憶）】\n{summary}\n\n"
                            
                        # 1. 組合近期未壓縮的記憶 (取最多 50 句)
                        history_rows = self.get_memory_with_ids(channel_id, limit=50)
                        history = [row[1] for row in history_rows]
                        if history:
                            history_lines = "\n".join(history)
                            prompt_text += f"【近期對話紀錄參考】\n{history_lines}\n\n"
                            
                        # 2. 加上這次發言者的內容
                        if user_msg:
                            prompt_text += f"【現在】[{current_user}]: {user_msg}"
                            self.add_memory(channel_id, f"[{current_user}]: {user_msg}")
                        elif message.attachments:
                            # 若使用者僅傳圖未打字
                            prompt_text += f"【現在】[{current_user}]: (傳送了一張圖片) 請發揮你的「限界社畜」人設，幫我狠狠評價一下這張圖片裡的東西！是罪惡的宵夜還是破壞心情的健康食物？"
                            self.add_memory(channel_id, f"[{current_user}]: (傳送了一張圖片)")
                            
                        contents.append(prompt_text)
                            
                        # 迴圈檢查附件是否為圖片
                        for attachment in message.attachments:
                            if attachment.content_type and attachment.content_type.startswith('image/'):
                                # 下載圖片資料轉為 bytes
                                image_bytes = await attachment.read()
                                contents.append({
                                    "mime_type": attachment.content_type,
                                    "data": image_bytes
                                })
                        
                        # 防呆機制：如果是文字跟非圖片附件，但根本沒有可以餵給模型的內容
                        if not contents:
                            return

                        # 丟進模型產生回覆！(Gemini 可以直接接收字串與圖片字典混合的 List)
                        response = self.model.generate_content(contents)
                        
                        # 回傳給 Discord
                        reply_text = response.text
                        await self.send_chunked_reply(message, reply_text)
                        
                        # 3. 把機器人自己的回覆也存進記憶裡
                        self.add_memory(channel_id, f"[限界社畜]: {reply_text.strip()}")
                        
                        # 4. 觸發滾動式摘要壓縮檢查 (放入背景執行，不卡住回應)
                        self.bot.loop.create_task(self.compress_memory(channel_id))
                        
                    except Exception as e:
                        await message.reply(f"✌🥺✌ 發生了一點錯誤... 難道是卡路里太高大腦被破壞了嗎？！ \n(Error: {e})")

    async def compress_memory(self, channel_id):
        try:
            # 取得目前所有的對話紀錄 (加大範圍到 50)
            history_rows = self.get_memory_with_ids(channel_id, limit=50)
            
            # 因為群組人多，累積少於 30 句先不壓縮，讓大家有足夠的即時上下文
            if len(history_rows) < 30:
                return
                
            print(f"[系統] 頻道 {channel_id} 對話超過 30 句，開始進行背景記憶壓縮...")
            # 找出這批對話中最新的 ID，等一下刪除時只刪到這個 ID，避免把壓縮期間新進來的對話刪掉
            last_id = history_rows[-1][0]
            history_text = "\n".join([row[1] for row in history_rows])
            
            old_summary = self.get_summary(channel_id)
            
            # 請 Gemini 幫忙做人物誌萃取 (專注於使用者特徵)
            prompt = (
                "你是一個專門記錄人類觀察日記的助手。以下是過去建立的【群組成員人物誌】，以及最新的一段聊天紀錄。\n"
                "請根據最新的對話，更新這份人物誌。你的唯一目標是『提取並記住每個人的特色與情報』，而不是記錄流水帳。\n"
                "請嚴格遵守以下規則：\n"
                "1. 僅紀錄少量的日常寒暄與無意義的對話內容，及少量記錄話題進度。\n"
                "2. 為每個發言過的使用者建立或更新獨立的條目（例如使用 `- [使用者名稱]: 喜歡... / 討厭... / 近況...` 的格式）。\n"
                "3. 確保將新發現的特徵融合進舊有的紀錄中，若某人沒有新情報也必須保留他的舊紀錄。\n"
                "4. 總長度請控制在 800 字以內。\n\n"
            )
            if old_summary:
                prompt += f"【過去的群組成員人物誌】\n{old_summary}\n\n"
            prompt += f"【最新對話紀錄】\n{history_text}\n\n請輸出更新後的人物誌："
            
            # 用同一個模型來做摘要
            response = self.model.generate_content(prompt)
            new_summary = response.text.strip()
            
            # 更新資料庫的摘要，並刪除已經壓縮過的對話
            self.save_summary(channel_id, new_summary)
            self.clear_history(channel_id, up_to_id=last_id)
            print(f"[系統] 頻道 {channel_id} 的記憶已壓縮完畢！摘要長度: {len(new_summary)} 字")
            
        except Exception as e:
            print(f"[系統] 壓縮記憶發生錯誤: {e}")

# 必須存在的 setup 函式，用來把這個 Cog 註冊進主程式中
async def setup(bot):
    await bot.add_cog(AIChat(bot))
