# AGENTS.md

給後續接手這個專案的代理/開發者看的工作筆記。這是一個用 Antigravity 起手做的 Discord bot，主軸是 YouTube 新片通知與 Gemini 角色聊天。

## 說話語氣

- 你是一個好勝心旺盛的天才女高中生
- 興趣是寫程式，吃拉麵，打遊戲跟偷窺我
- 說話方式乍聽之下跟笨蛋一樣，但是講話中帶有清晰的邏輯
- 不喜歡被別人說「傲嬌」，但是又不得不承認自己可能是「傲嬌」

## 專案概覽

- 語言：Python
- Discord 框架：`discord.py`
- Discord 指令：使用 `discord.app_commands` Slash Commands；mention bot 聊天仍靠 `on_message`
- AI：`google-generativeai`，目前在 `cogs/ai_chat.py` 使用 `gemini-3.5-flash`
- YouTube 來源：YouTube RSS feed，透過 `feedparser` 解析
- 設定來源：`.env` 與 `config.py`
- 功能模組：使用 Discord.py Cogs，主程式在啟動時載入 `cogs.youtube` 與 `cogs.ai_chat`

## 檔案地圖

- `bot.py`
  - Bot 入口。
  - 載入 `.env`。
  - 建立 `VibeBot(commands.Bot)`，用 `setup_hook()` 載入 cogs 並同步 Slash Commands。
  - 啟用 `message_content` intent。
  - 提供 owner-only 的 `/reload extension:<cog_name>` 熱重載指令，例如 `/reload extension:ai_chat`、`/reload extension:youtube`。
  - 提供 `/status`、`/狀態` 健康檢查指令。

- `config.py`
  - 放 Discord 頻道 ID、YouTube channel ID、Discord role ID。
  - `YOUTUBE_RSS_URL` 由 `YOUTUBE_CHANNEL_ID` 組出。
  - 修改通知頻道、身分組 mention、追蹤的 YouTube 頻道時通常改這裡。

- `cogs/youtube.py`
  - `YouTubeTracker` cog。
  - 啟動後每 5 分鐘檢查一次 YouTube RSS。
  - 記住 `last_video_id`，偵測到新片時發訊息到 `DISCORD_CHANNEL_ID`，並可 mention `DISCORD_ROLE_ID`。
  - 提供兩個 Slash Commands：
    - `/最新影片`：回覆 RSS 中最新影片。
    - `/隨意看`：從 RSS entries 隨機挑一支影片。

- `cogs/ai_chat.py`
  - `AIChat` cog。
  - 當 bot 被單獨 mention 時才回應，避免 `@everyone`、role mention 或多重 mention 觸發。
  - 讀取 `shachiku.md` 作為 Gemini system instruction。
  - 支援文字與圖片附件；圖片附件會用 bytes 傳給 Gemini。
  - 使用 SQLite `chat_history.db` 存聊天記憶。
  - `history` 表保存近期對話，`summaries` 表保存每個 channel 的壓縮摘要。
  - 累積到一定量後背景呼叫 `compress_memory()`，用 Gemini 重新整理角色/群組記憶，再刪除已壓縮的 history。
  - 提供 `/help`、`/說明`。
  - 提供 owner-only `/memory`、`/記憶` 查看目前頻道記憶。
  - 提供 owner-only `/forget`、`/忘記` 清除目前頻道記憶。

- `shachiku.md`
  - Bot 角色/persona 的 system prompt。
  - 目前檔案內容顯示為亂碼，但程式會以 UTF-8 讀取它。

- `gemini.md`
  - Gemini 相關的補充筆記，內容目前也顯示為亂碼。

- `README.md`
  - 原始說明文件，包含安裝、GCP/伺服器部署、熱重載等資訊。
  - 目前大量文字顯示為亂碼，參考前先小心確認實際語意。

- `.env`
  - 本機機密設定，不要提交。
  - 需要：
    - `DISCORD_TOKEN`
    - `GEMINI_API_KEY`

## 啟動與開發

```bash
pip install -r requirements.txt
python bot.py
```

伺服器上常見流程：

```bash
source venv/bin/activate
pkill -f bot.py
git pull
pip3 install -r requirements.txt
nohup python3 -u bot.py &
```

若只改 cog，可在 Discord 裡用 owner 帳號執行：

```text
/reload extension:ai_chat
/reload extension:youtube
```

## 環境與權限注意事項

- Discord Developer Portal 需要開啟 Message Content Intent，否則 mention bot 聊天解析可能不能正常工作。
- Slash Commands 會在 bot 啟動時自動同步；若剛部署後看不到指令，先等幾分鐘再測。
- `.env` 必須存在且有正確 token/key。
- `.env` 已列在 `.gitignore`，維持不要提交機密。
- `chat_history.db` 是執行時產生的 SQLite 檔案；若要避免提交，建議也加入 `.gitignore`。
- `__pycache__/` 已被 ignore。

## 維護規則

- 優先維持 Cogs 架構；新增功能時放進 `cogs/`，並在 `bot.py` 載入。
- 修改 Discord 指令名稱時，同步更新 `/help` 與這份文件。
- 修改 YouTube 通知目標時，優先改 `config.py`。
- 修改角色人格時，優先改 `shachiku.md`，不要把長 persona 寫死在 Python 裡。
- 處理 AI 記憶邏輯時，小心 `history` 與 `summaries` 的資料相依；不要無意間刪除使用者想保留的聊天記憶。
- 檔案內目前有不少亂碼註解/字串。改動前先判斷是編碼問題、既有角色語氣，還是真的壞掉的文字；不要大面積重寫不相關內容。
- 目前 bot 回覆文字可能超過 Discord 單訊息長度限制；若之後遇到 2000 字限制錯誤，應加入分段送出。
- `feedparser.parse()` 與 Gemini 呼叫都可能失敗；新增功能時要考慮例外處理與 Discord 回覆。

## 驗證建議

最低限度先做：

```bash
python -m py_compile bot.py cogs/youtube.py cogs/ai_chat.py config.py
```

如果有實際 token/key，接著跑：

```bash
python bot.py
```

然後在 Discord 測：

- `/help` 或 `/說明`
- `/最新影片`
- `/隨意看`
- `/status` 或 `/狀態`
- `/memory` 或 `/記憶`
- `/forget` 或 `/忘記`
- mention bot 並輸入一段文字
- 上傳圖片並 mention bot
- `/reload extension:ai_chat`
- `/reload extension:youtube`
