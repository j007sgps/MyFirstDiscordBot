# MyFirstDiscordBot (限界社畜與 YouTube 通知小幫手)

這是一個以 Discord.py 為基礎、結合 Google 最新 Gemini 3.1 語言模型打造的專屬生態系機器人。它擁有強烈的「限界社畜」與「喜愛背德美食」的深夜人設，不只會自己報時，還能成為深夜的宵夜最佳聊伴！

## ✨ Bot 的主要功能

本機器人已經針對後端架構進行 **Cogs 模組化升級**，確保各項功能運作穩定：

- 📺 **YouTube 新影片自動推播 (`cogs/youtube.py`)**：
  - 每 5 分鐘自動巡邏一次。若有新影片，會立刻帶上「崩潰社畜專屬顏文字與誇飾抱怨」推播到 Discord 頻道，支援自動標記身分組功能。
  - 已通知過的影片會記錄在本機 SQLite `bot_state.db`，避免 GCP 重啟或熱重載後又重複推播同一支影片。
- 🍔 **深夜互動對話指令**：
  - `!最新影片`：幫你抓出頻道目前最新發布的「大腦破壞」級美食影片。
  - `!隨意看`：不知道看什麼？讓機器人為你隨機抽選一場罪惡之宴。
  - `!status` / `!狀態`：查看 bot 上線時間、Discord 延遲、模組載入狀態、YouTube 巡邏任務與 SQLite 資料筆數。
  - `!help` (或 `!說明`)：喚出專屬的指令選單。
- 🤖 **[最新] 靈魂注入 AI 自由聊天 (`cogs/ai_chat.py`)**：
  - 只要在群組內直接 @標記 機器人，它就會讀取專屬的 `shachiku.md` 潛意識（System Instructions），使用 **Gemini 3.1 Flash** 的高速大腦，以「限界社畜」的誇張語氣與你自然對話！
  - Gemini 回覆超過 Discord 單則訊息長度時，會自動分段送出，避免 2000 字限制造成送訊息失敗。
  - `!memory` / `!記憶`：擁有者限定，查看目前頻道的 AI 長期摘要與近期未壓縮對話。
  - `!forget` / `!忘記`：擁有者限定，清除目前頻道的 AI 記憶與摘要，測試人格或重置對話狀態時很好用。

---

## 💻 本機開發環境設定

如果你要在自己電腦上開發或測試機器人，請先確認以下的設定：

1. **設定環境變數**：在專案根目錄下建立 `.env` 檔案，填入你的兩種金鑰密碼（請確保不要上傳到 GitHub）：
   ```env
   DISCORD_TOKEN=你的機器人Token密碼
   GEMINI_API_KEY=你的GoogleAI_Studio金鑰
   ```
2. **安裝所需套件模組**：
   ```bash
   pip install -r requirements.txt
   ```
3. **啟動測試**：
   ```bash
   python bot.py
   ```

---

## 🔐 GCP 雲端主機：設定或更新密碼檔 (`.env`)

因為出於安全考量，我們的密碼檔被 `.gitignore` 隔離，並不會被 GitHub 備份。這代表**如果你的密碼換了，或是有了新的 API Key（例如新加入的 Gemini），你必須親自在 GCP 雲端主機上手動編輯這個檔案。**

你可以使用 Linux 內建的 `nano` 文字編輯器來達成：

1. 確保你已經進入專案目錄 (`cd MyFirstDiscordBot`)。
2. 輸入指令呼叫文字編輯器來打開它：
   ```bash
   nano .env
   ```
3. 這時候終端機會變成像是記事本的編輯模式，請把你的密碼貼進去（或修改原本的）：
   ```env
   DISCORD_TOKEN=你的機器人密碼
   GEMINI_API_KEY=你的Google_API密碼
   ```
4. **存檔與離開的三部曲**：
   - 按下 `Ctrl + O` 準備寫入存檔 (字母O)
   - 按下 `Enter` 確認檔名不變
   - 按下 `Ctrl + X` 退出此程式

完成修改後，就可以繼續執行下方的「更新指令紀錄」第五步（重啟機器人）來套用新密碼了！

---

## 🧠 執行時資料庫

機器人會在執行時自動建立以下 SQLite 檔案，這些檔案已經加入 `.gitignore`，不要提交到 GitHub：

- `chat_history.db`：AI 聊天記憶、近期對話與每個頻道的壓縮摘要。
- `bot_state.db`：YouTube 已通知影片紀錄，用來防止重啟後重複通知。

如果在 GCP 上刪除這些檔案，機器人會自動重建；但同時也會失去 AI 記憶或 YouTube 已通知狀態。哼，所以不要亂刪，除非你真的想讓它失憶。

---

## ☁️ GCP 雲端主機：更新指令紀錄 (🌟 核心必備)

每次你在本機（自己電腦）修改好程式碼（例如擴充 `shachiku.md` 讓它講話更好笑），並成功 `git push` 後，請打開 GCP 的黑色 SSH 終端機，**依序**貼上以下 5 句指令，就能無痛重啟機器人回背景值班：

```bash
# 1. 走進你的專案房間
cd MyFirstDiscordBot

# 2. 啟動虛擬沙盒環境 (注意左邊有沒有出現 (venv) 字樣)
source venv/bin/activate

# 3. 把原本還在偷偷躲著跑的舊版機器人強制關機
pkill -f bot.py

# 4. 從 GitHub 把修改好的全新程式碼拉下來 (覆蓋更新)
git pull

# 5. [新加入必做] 讓雲端主機安裝清單內剛新增的套件 (例如這次的 google-generativeai)
pip3 install -r requirements.txt

# 6. 讓新版的機器人重新回到背景 24 小時上班
nohup python3 -u bot.py &
```

> 💡 **進階小提點**：如果想偷看有沒有人半夜在玩機器人的 AI 聊天，可以隨時在終端機輸入 `cat nohup.out` 或是即時監控 `tail -f nohup.out` 來查看它的日記本（Console Log）！

> 💡 **這次更新提醒**：本次新增的是 Python 程式邏輯與 SQLite 狀態表，沒有新增套件；GCP 上 `git pull` 後，可用下方 Hot Reload 重新載入 `ai_chat` 與 `youtube`，或直接完整重啟 bot。

> ⚠️ `!status` 是加在 `bot.py` 的主程式指令；如果這次要讓 GCP 上的 bot 取得它，請完整重啟 bot，不能只使用 Hot Reload。

---

## 🔥 進階魔法：不斷線的熱重載 (Hot Reload) 更新

既然我們已經擁有高級的 **Cogs 模組化架構**了，未來如果你**只修改了** `cogs/ai_chat.py` 或 `cogs/youtube.py`，你不用再像以前那樣把機器人關機（`pkill`）了。

你可以使用這個專屬擁有者的「微創手術」：
1. 進入 GCP，照常執行 `cd MyFirstDiscordBot` 和 `git pull` 把程式碼拉下來。
2. （**不需要**使用 pkill 與 nohup）
3. 直接在你的 Discord 聊天室裡，對著機器人輸入專屬隱藏指令：
   - 重新載入 AI 對話腦：`!reload ai_chat`
   - 重新載入 推播功能：`!reload youtube`

機器人會直接不掉線、1 秒內把新的大腦（或 YouTube 齒輪）換上。如果成功，它會回覆你 `✅ 模組已經重新載入完成！`這對於以後天天維護升級來說超級方便！
