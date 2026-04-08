import discord
from discord.ext import commands
import google.generativeai as genai

class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
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

    # 取代原本在 bot.py 裡的 !help
    @commands.command(name="help", aliases=["說明"])
    async def custom_help(self, ctx):
        help_text = (
            "✌🥺✌ **為了健康生活，深夜就該吃背德美食！！** \n\n"
            "🍟 **你可以對我下達這些罪惡的指令：**\n"
            "▶️ `!最新影片`：讓我端上剛出爐、熱量爆表的最新發布影片！\n"
            "▶️ `!隨意看`：深夜不知道看什麼？讓我隨機為你挑一場破壞大腦的罪惡之宴～\n"
            "▶️ `!help` 或 `!說明`：呼叫這個說明選單。\n\n"
            "🤖 **[新功能！] 靈魂注入的 AI 聊天**：直接在群組裡標記我 (@限界社畜！！！)，然後跟我講話吧！\n\n"
            "💡 *當然啦，只要有新影片發布，我也會第一時間通知做個吃貨傢伙！是真的假的？？？*"
        )
        await ctx.reply(help_text)

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
            
            # 檢查是否有文字或圖片附件
            if user_msg or message.attachments:
                # 顯示 "機器人正在輸入..." 的狀態
                async with message.channel.typing():
                    try:
                        # 準備要傳遞給 Gemini 的內容清單
                        contents = []
                        if user_msg:
                            contents.append(user_msg)
                        elif message.attachments:
                            # 若使用者僅傳圖未打字，給予預設情境引導
                            contents.append("請發揮你的「限界社畜」人設，幫我狠狠評價一下這張圖片裡的東西！是罪惡的宵夜還是破壞心情的健康食物？")
                            
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
                        await message.reply(response.text)
                    except Exception as e:
                        await message.reply(f"✌🥺✌ 發生了一點錯誤... 難道是卡路里太高大腦被破壞了嗎？！ \n(Error: {e})")

# 必須存在的 setup 函式，用來把這個 Cog 註冊進主程式中
async def setup(bot):
    await bot.add_cog(AIChat(bot))
