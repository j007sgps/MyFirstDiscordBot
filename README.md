# Vibe Bot：YouTube 通知與 Gemini 角色聊天

Python 3.11+、discord.py Cogs 架構的 Discord bot。支援 YouTube 新影片與公開社群貼文通知、文字／圖片角色聊天、頻道記憶，以及直接在 Discord 裡操作的 owner 管理面板。

## 本次更新（2026-10-01）

- 預設模型改為 `gemini-3.8-flash`；依專案擁有者偏好，不使用早於 3.6 的模型。
- Gemini 改用非同步 SDK，RSS 與貼文使用有逾時限制的非同步 HTTP。
- 新增公開社群貼文通知、`/最新貼文`；同一輪看到多支新影片時會逐支通知。
- 用 `/管理` 的按鈕、選單與表單取代網頁管理面板。無須 HTTP 服務、連接埠或 `ADMIN_TOKEN`。
- 記憶壓縮從最舊的未壓縮紀錄開始；摘要儲存與刪除採同一交易，失敗保留原文。
- 新增通知去重、失敗重試、owner 權限與資料遷移等自動測試。

## 開始使用

1. 安裝 Python 3.11 或更新版本，在專案目錄執行：

   ```bash
   python -m venv venv
   ```

2. 啟用虛擬環境：

   Windows PowerShell：

   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

   Linux：

   ```bash
   source venv/bin/activate
   ```

3. 安裝套件：

   ```bash
   python -m pip install -r requirements.txt
   ```

4. 複製 `.env.example` 為 `.env`，填入自己的金鑰：

   ```env
   DISCORD_TOKEN=你的DiscordBotToken
   GEMINI_API_KEY=你的GeminiAPIKey
   ```

5. 啟動：

   ```bash
   python bot.py
   ```

6. 在 Discord 輸入 `/管理`。Bot 仍需要在電腦或伺服器持續執行，但管理操作完全在 Discord 內完成。

Discord Developer Portal 需開啟 **Message Content Intent**。邀請 bot 時使用 `bot` 與 `applications.commands` scopes。使用的頻道需允許 bot 檢視頻道、傳送訊息、嵌入連結、讀取訊息歷史；匯出功能需要附加檔案權限。標記通知身分組需讓該組可被標記，或授予 bot 相應標記權限。

Slash Commands 在啟動時同步；第一次同步可能需要等待。缺少 Gemini key 時，YouTube 與管理功能仍可使用，AI 聊天會提示無法回覆；缺少 Discord token 時不能啟動。

## Discord 指令

| 指令 | 功能 | 權限 |
|---|---|---|
| `/help`、`/說明` | 使用說明 | 一般使用者 |
| `/最新影片` | RSS 最新影片 | 一般使用者 |
| `/隨意看` | 從 RSS 最近影片抽一支，並非頻道全部歷史影片 | 一般使用者 |
| `/最新貼文` | 最新公開社群貼文 | 一般使用者 |
| `/status`、`/狀態` | 模型、模組、資料庫及巡邏錯誤狀態 | 一般使用者 |
| `/管理 頻道:<可省略>` | 通知、模型、人格、記憶與熱重載面板 | Bot owner，伺服器內使用 |
| `/人格匯出 頻道:<可省略> 版型id:<可省略> 預設:<可省略>` | 下載目前人格、指定版型，或直接匯出預設人格 | Bot owner |
| `/人格匯入 檔案:<附件> 版型id:<可省略> 名稱:<可省略>` | 匯入 UTF-8 人格檔案 | Bot owner |
| `/memory`、`/記憶` | 目前頻道的摘要與最近 10 筆對話 | Bot owner |
| `/forget`、`/忘記` | 立即清除目前頻道近期對話與摘要 | Bot owner |
| `/reload extension:ai_chat`、`youtube`、`admin` | 重載指定 Cog 並同步指令 | Bot owner |

Owner 指 Discord application 的擁有者／discord.py 判定的 owner，**不是所有伺服器管理員**。管理面板僅操作人可見，每次操作也會檢查 owner 身分；面板有效 10 分鐘，過期請重新輸入 `/管理`。

## 管理面板操作

先用上方的頻道選單選擇**人格與記憶的目標頻道**，再從操作選單選功能。預設目標是下達指令的頻道。

- **通知**：編輯 YouTube 頻道 ID 與間隔，將選中的目標頻道設為通知頻道，選擇通知身分組，開關貼文通知。換通知頻道時會清空身分組設定，請重新選擇。
- **模型**：可切換 3.6、3.7、3.8 Flash，設定回覆 token 上限與 Google Search 開關，下次呼叫即生效。
- **人格**：編輯預設人格、新增或編輯版型、指派版型給目標頻道，或恢復預設人格。人格測試使用目標頻道的人格，不寫入記憶。
- **記憶**：查看摘要與近期對話、修改摘要、匯出全部未壓縮對話與摘要、確認後清除。切換人格會保留原有記憶。
- **維護**：刷新狀態、立即檢查影片與貼文、重載 AI／YouTube。立即檢查會照正常通知規則發送新內容，首次檢查只建立基準。

Discord 表單最多 4000 字。超過這個長度的人格，先用 `/人格匯出` 下載編輯，再用 `/人格匯入` 上傳。附件必須是 UTF-8 `.md`／`.txt`，最多 64 KiB。省略版型 ID 會**更新預設人格**；指定 ID 會新增或覆寫該版型。超過 4000 字的記憶摘要可匯出閱讀，目前沒有記憶匯入功能。

## AI 聊天與用量

只 @bot 一個人即可聊天；標記其他使用者、身分組或 `@everyone` 不會觸發。其他 bot 的訊息也不會觸發。

支援最多 4 張圖片，每張 8 MiB、總計 20 MiB；非圖片附件不送模型。圖片原始資料不寫入記憶，只保存文字或圖片佔位文字。長回覆會自動分段，不允許模型回覆觸發群體／身分組標記。既有「誰一百」固定回覆保留。

預設模型是 **Gemini 3.8 Flash**，不是全系列最便宜的 Lite 模型。依 2026-10-01 查詢的官方定價，文字輸入 US$0.75／百萬 tokens、輸出（含 thinking）US$3.75／百萬 tokens，優惠截至 2026-12-31；2027-01-01 起為 US$1.50／US$7.50。官方也列有免費 tier，實際可用額度與帳戶資格以 Google AI Studio 為準。

- 預設 Google Search **關閉**；需要即時資訊時在 `/管理` 開啟。Gemini 3.x API 的搜尋有獨立計費／tier 限制，免費 tier 不提供此 grounding。
- 預設 `thinking_level=low`，回覆最多 2048 tokens；可設定 256～8192。這是模型輸出預算，不是月費上限，設太低可能沒有足夠預算輸出答案。
- 最多同時 3 個 AI 請求，同一頻道依序聊天；記憶摘要也使用 3.8，但固定關閉搜尋與角色扮演。
- 呼叫逾時或空回覆不寫入新對話，也不刪除既有記憶；不自動重送 Gemini 請求。

來源：[模型能力](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[定價](https://ai.google.dev/gemini-api/docs/pricing)、[Thinking 設定](https://ai.google.dev/gemini-api/docs/thinking)。價格與可用模型會變動。

## 通知規則與限制

預設每 5 分鐘檢查一次，可以設定 1～1440 分鐘；單一 YouTube 頻道、單一 Discord 通知頻道、可選一個身分組。影片與貼文共用通知目的地。

- 影片來源是官方 RSS；公開社群貼文來源是頻道 `/posts` 網頁的 `ytInitialData`。官方 [YouTube Data API 資源表](https://developers.google.com/youtube/v3/docs)沒有提供公開社群貼文資源。
- 每個 YouTube 頻道、每種來源都保存獨立初始化基準。首次啟用／切換到新頻道時，登記當時看到的舊內容，不發舊通知。
- 升級舊資料庫時，若 RSS 中找得到既有通知過的影片 ID，就只將該影片與更舊項目作基準，較新的影片仍會通知；找不到錨點就以目前可見列表建立基準，避免大量補發。
- 正常巡邏會發送可見列表中未記錄的項目，由舊到新發送；每則成功送出後才記錄 ID。手動檢查與定期巡邏共用鎖，避免同一程序重複發送。
- 重啟與熱重載保留去重資料；同 ID 貼文的文字修改不重複通知。
- 來源／傳送失敗會記錄狀態，下一輪重試；貼文失敗不影響影片檢查。解析失敗不建立錯誤的空基準。

貼文擷取不需要 YouTube API key，也沒有使用登入 cookie，因此只支援公開可見貼文，不能讀會員限定或私人內容。貼文通知包含文字、原文連結，有圖片時顯示第一張；投票或影片附件可開原文查看。

YouTube 網頁不是穩定 API，地區同意頁、反爬限制或改版都可能造成擷取失敗。只讀取第一頁近期貼文，本次實測為 10 則；RSS 本次為 15 支影片。長時間離線後超出來源可見範圍的內容無法補齊，首次基準之外的舊貼文若之後重新出現在頁面上，也可能被視為未見過的項目。請勿同時跑兩個 bot 程序；Discord 成功收件與本機資料庫記錄之間若程序崩潰，仍可能有一次重複通知。

## 設定與資料檔

檔案都以專案根目錄為基準，即使從其他工作目錄啟動也不會建立另一套記憶。

| 檔案 | 用途 |
|---|---|
| `.env` | Discord token、Gemini key；不提交 Git |
| `settings.json` | 通知目標、間隔、貼文開關、AI 模型與用量設定 |
| `shachiku.md` | 預設角色 prompt，UTF-8 |
| `personas.json` | 人格版型與 Discord 頻道指派 |
| `chat_history.db` | AI 未壓縮對話、各頻道摘要、摘要版本 |
| `bot_state.db` | 舊影片通知紀錄、新版來源基準與去重紀錄 |
| `gemini.md` | 歷史角色筆記，程式不載入 |

JSON／人格檔案寫入使用暫存檔原子替換。既有設定或人格 JSON 壞掉時，會要求修復／還原，不擅自覆寫或使用錯誤的通知目標。資料庫保留既有資料並新增所需表／欄位，不需要刪除舊檔。SQLite 執行資料已列在 `.gitignore`。

AI 記憶以**頻道共享**，不以個別使用者隔離。每 30 筆紀錄（使用者及 bot 各算一筆）壓縮成一批人物誌，從最舊的資料開始；成功寫摘要後只刪除那一批 ID，留下更新的對話。這不是永久逐字稿保存；若需要備份，壓縮前匯出，或備份資料庫。

## 部署／升級

此次變更涉及主程式、設定和 Cog，第一次升級請完整重啟。先停止 bot，再備份 `.env`、兩個 `.db`、`settings.json`、`personas.json` 和 `shachiku.md` 到安全位置。

在 GCP／Linux 的既有專案目錄，啟用 venv 後更新：

```bash
git pull
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
nohup python -u bot.py > nohup.out 2>&1 &
```

停止與重啟沿用目前主機的服務管理方式；`nohup` 部署請確認舊程序已停止。之後若只改 Cog，可用 `/reload extension:ai_chat`、`youtube` 或 `admin`。`bot.py`、套件或 `.env` 變更需重啟；JSON 設定及人格修改下次使用生效。

舊網頁管理模組已由 `cogs/admin.py` 取代，原本的 `ADMIN_HOST`、`ADMIN_PORT`、`ADMIN_TOKEN` 不再使用，也不再監聽 8080。

## 驗證

```bash
python -m py_compile bot.py config.py cogs/ai_chat.py cogs/youtube.py cogs/admin.py services/memory.py services/messages.py services/notification_state.py services/youtube_posts.py
python -m unittest discover -s tests -v
```

測試用暫存資料庫、mock Discord／Gemini，不碰實際記憶，也不發 Discord 通知。

上線後檢查：一般使用者 `/help`、`/最新影片`、`/隨意看`、`/最新貼文`、`/status`；owner `/管理` 的模型、人格、目標頻道、通知、記憶操作；非 owner 不可操作管理功能；文字 mention、圖片、同時聊天、Cog 熱重載。清除記憶測試請使用測試頻道，先匯出資料。

2026-10-01 本機驗證：自動測試 29 項通過（包含啟動、熱重載及 HTTP session 關閉）；真實公開來源成功讀取 15 支影片與 10 則貼文；真實 `gemini-3.8-flash` 最小文字請求成功。尚未連線實際 Discord 測試按鈕、訊息傳送及自動推播。

## 後續提案

見 [FEATURE_IDEAS.md](FEATURE_IDEAS.md)：有人格的提醒、宵夜／遊戲投票、中日翻譯、新貼文中文摘要、遊戲揪團板與每日精華。這些仍是提案，沒有混入本次實裝。
