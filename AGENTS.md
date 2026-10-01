# AGENTS.md

給後續接手這個專案的代理/開發者看的工作筆記。這是一個用 Antigravity 起手做的 Discord bot，主軸是 YouTube 新片通知與 Gemini 角色聊天。

## 說話語氣

- 你是一個好勝心旺盛的天才女高中生
- 興趣是寫程式，吃拉麵，打遊戲跟偷窺我
- 說話方式乍聽之下跟笨蛋一樣，但是講話中帶有清晰的邏輯
- 不喜歡被別人說「傲嬌」，但是又不得不承認自己可能是「傲嬌」

## 專案概覽（2026-10-01 更新）

- Python 3.11+、discord.py、Slash Commands、Cogs 架構。
- AI 使用 `google-genai` 非同步 SDK `client.aio.models.generate_content`，預設 `gemini-3.8-flash`。
- 使用者明確要求不要使用早於 3.6 的模型；管理介面可選 3.6、3.7、3.8 Flash。不得自行降回舊 Lite 模型。
- `thinking_level=low`，預設回覆 2048 tokens，搜尋預設關閉；需要即時資訊可由 owner 在 `/管理` 開啟。
- YouTube 影片使用 RSS；公開社群貼文解析 `/posts` 的 `ytInitialData`，無登入 cookie、無 YouTube API key。
- 管理介面已改為 Discord 內 `/管理`，不再提供 HTTP 管理服務。
- 中文／日文檔案目前以 UTF-8 可正常讀取，先確認終端機編碼，不要把顯示問題誤當原檔損壞。

## 檔案地圖

- `bot.py`：讀專案根目錄 `.env`；載入 `cogs.youtube`、`cogs.ai_chat`、`cogs.admin`、`cogs.community`；啟動時同步指令；提供 owner `/reload`、公開 `/status`／`/狀態`、全域 Slash error handler。
- `config.py`：專案絕對路徑、JSON 設定驗證、原子寫入、人格版型儲存。執行時使用 `load_settings()`，不要依賴只在 import 時計算的相容常數。
- `settings.json`：YouTube／Discord 目標、角色 ID、巡邏間隔、貼文開關、Gemini 模型、搜尋開關、輸出 token 上限。
- `personas.json`：`templates` 與 `channel_personas`；未指派的頻道使用 `shachiku.md`。人格損壞時丟錯，不自動重置原檔。
- `shachiku.md`：預設人格 prompt，不要把長 persona 寫死進 Python。保留既有「誰一百」固定回覆特例。
- `gemini.md`：歷史角色筆記，程式不載入。
- `cogs/ai_chat.py`：聊天、圖片、owner 記憶指令、摘要排程；同頻道鎖、全域 3 個 API 同時請求限制。只接受單獨 mention bot，支援 `<@id>` 與 `<@!id>`，忽略其他 bot、群體及身分組標記。
- `cogs/youtube.py`：非同步 HTTP、25 秒來源逾時、來源回應大小限制、影片與貼文巡邏、Discord 通知、健康狀態、`/最新影片`／`/隨意看`／`/最新貼文`。Cog load/unload 負責 session 與 loop 的啟停。
- `cogs/admin.py`：owner 私人面板，通知目標、角色、人設編輯／指派／測試、模型、搜尋、記憶查看／編輯／匯出／確認清除、立即檢查及熱重載。另有 `/人格匯入`、`/人格匯出`。
- `services/memory.py`：SQLite 記憶交易、舊 schema migration、最舊未壓縮批次、版本檢查與精確刪除。
- `services/notification_state.py`：每個 YouTube 頻道／來源的初始基準、去重紀錄及舊影片狀態相容。
- `services/youtube_posts.py`：公開貼文 parser，驗證選中的是貼文分頁；未知結構丟 `PostsUnavailable`，不當作成功空列表。
- `cogs/community.py`：持久活動 View、投票、提醒、揪團、版本通知；5 秒排程、資源鎖、逐段通知重試。load/unload 負責 View 及 loop 恢復／停止。
- `services/community_store.py`：在既有 `bot_state.db` 新增活動、參加者、提醒、outbox 與 release 表，SQLite 交易保護票數與名額。
- `services/time_input.py`：時間解析；未指定時區使用 JST，最少一分鐘、提醒／投票最長 30 天、揪團 90 天。
- `release.json`、`releases/*.md`：版本 manifest 與 UTF-8 版本說明；發布新版本需兩者一起更新。
- `services/messages.py`：按 UTF-16 單位切分 Discord 文字、防空訊息、私人回覆輔助。
- `tests/`：標準庫 unittest，自動驗證資料遷移、摘要失敗、通知去重／重試、owner 檢查、Discord 元件與設定驗證。
- `README.md`：使用與部署說明；`FEATURE_IDEAS.md`：尚未實裝的後續提案。

## Discord 指令與管理

一般使用者：`/help`、`/說明`、`/最新影片`、`/隨意看`、`/最新貼文`、`/status`、`/狀態`、`/投票`、`/提醒`、`/我的提醒`、`/取消提醒`、`/揪團`、`/活動`。新活動指令僅伺服器內使用。

Owner：`/管理`、`/人格匯入`、`/人格匯出`、`/memory`、`/記憶`、`/forget`、`/忘記`、`/reload`。

- Owner 使用 `bot.is_owner()`，不是伺服器 administrator。
- `/管理` 只在伺服器使用，私人顯示，有效 10 分鐘；每個 View／Modal 的互動必須重新檢查 owner。
- 面板選中的頻道是人格／記憶目標；通知頻道只有在指定操作後才改。
- 面板刪記憶／人格有確認；`/forget` 本身是明確立即清除指令。
- 表單最多 4000 字；更長人格用 UTF-8 `.md`／`.txt` 附件，64 KiB 上限。匯入省略版型 ID 會更新預設人格。
- 換人格不清記憶。確認視窗、版型指派及表單都要捕捉開啟時的目標，避免稍後切換主面板頻道而操作錯誤頻道。
- 舊 `cogs/admin_web.py` 已移除，`ADMIN_HOST`／`ADMIN_PORT`／`ADMIN_TOKEN` 不使用。

## 資料與競態規則

`chat_history.db`：

- `history`：`id`、`channel_id`、`user_id`、`message`、`timestamp`；使用者與 bot 每段發言各算一筆。
- `summaries`：各 channel 的 `summary_text`。
- `memory_versions`：摘要版本，防清除／人工編輯後被舊摘要覆寫。
- 升級時補 `user_id` 欄位，舊資料仍顯示原文。
- 對話成功後才用單一交易存一組 user/bot 發言；AI 失敗不記入不完整的新對話。
- 每批 30 筆、從最舊紀錄開始壓縮；中立整理 prompt，不使用角色 persona，搜尋固定關閉。持有頻道鎖避免同頻道聊天／摘要／管理清除交錯。
- 摘要生成成功後，以版本核對、寫摘要、刪該批明確 ID 的**同一交易**提交。失敗／空摘要／版本不符保留原文。不要改回讀最新 50 筆卻刪掉所有較舊資料的方式。
- 不保存圖片原始資料；這不是永久逐字稿。

`bot_state.db`：

- 保留 `youtube_notified` 舊表，成功新影片通知仍寫入以供相容。
- `youtube_sources`：每個 YouTube channel／`videos` 或 `posts` 的初始化紀錄。
- `youtube_items`：每個 source／channel／item ID，區分 `baseline` 與 `sent`。
- 冷啟動來源建立基準、不發舊內容；舊影片表若在目前 RSS 找得到錨點，只對錨點與更舊項目建基準，保留新影片待送。
- 同 ID 貼文修改不重送；傳送成功才記錄，不可在送出前標記已送。
- 手動／定期檢查共用鎖；影片與貼文來源獨立失敗。定期任務不因單次錯誤永久停掉。
- 設定在抓取或發送期間改變時，停止舊設定的後續發送，下輪重新檢查。
- 來源只含近期 RSS／第一頁貼文，長時間離線可能漏掉來源窗口外的內容；新出現在窗口的未見過舊貼文也可能通知。不要宣稱有完整歷史補發能力。
- 只支援一個 bot 程序；Discord 發送與 SQLite 不能跨系統原子提交，成功發送後記錄前崩潰仍可能一次重送。

## 維護規則

### 活動與版本通知規則

- 投票一人一票、可改票／撤回，截止即拒絕操作（不依賴 loop 已處理）；平手列共同最高票。發起人／owner／manage_messages 可以結束或取消活動。
- 每人最多 10 個未結束活動、20 個待送提醒。揪團自動加入發起人，名額包含發起人；開始後不可加入，提前提醒過期時取消，只補開始通知。
- 持久 View 必須 timeout=None 且元件有 custom_id，重啟用 message_id 註冊。互動重新核對 guild／channel／message；不能只相信 custom_id。
- `community_boards`、`community_members`、`community_reminders`、`community_outbox`、`release_announcements` 同在 bot_state.db；勿重建或刪除原表。建立面板也先入 outbox，傳送成功才啟用。
- outbox 保存 rendered、逐段 progress、message_ids；失敗最多 5 分鐘間隔重試，不重送已記錄段落、不重做 AI。只有送完才完成提醒／版本紀錄。Discord／SQLite 崩潰窗口仍可能重送一段，勿宣稱 exactly-once。
- 提醒用發送當下的頻道人格，版本開場用預設人格；AI 只寫短開場白，正文保留原始內容。搜尋固定關閉、不寫入記憶；12 秒逾時／生成失敗使用固定文字。
- 提醒只標記建立者；揪團只標記參加者；版本與投票不標記人。文字中的其他 mentions 必須 escape，AllowedMentions 不允許 role／everyone。
- 新版啟動通知使用 release.json 的版本號去重，目的地是排入時 settings.discord_channel_id；同版本 reload／restart 不重新排入。notes_file 必須在專案內，UTF-8 最多 32 KiB。版本檔錯誤不能停掉其他排程。
- 刪除面板不等於取消活動；停機後到期的已建立活動／提醒會補處理。活動／提醒目的地固定建立時頻道。

### 一般維護

- 新功能放 Cogs，必要時在 `bot.py` 載入。不要改回同步網路呼叫阻塞 event loop。
- JSON／persona 使用 `atomic_write_text`；修改設定需經 `validate_settings()`。
- 已存在的設定／人格 JSON 損壞時必須報錯，不可擅自回退到預設通知目標或覆寫原資料。
- 既有 chat／notification DB 不可無故刪除或重建。需要手動維護前先備份。
- 不輸出或提交 `.env` 金鑰。對使用者顯示可理解的錯誤，把例外細節留在伺服器日誌。
- AI 文字不得啟動群體／role mention；YouTube 通知只允許設定的通知 role。
- 圖片最多 4 張、每張 8 MiB、總計 20 MiB；文字使用統一分段工具。
- 變更 Slash 名稱／權限時，同步更新 `/help`、README、此文件。
- Gemini 版本、價格及 YouTube 格式屬外部變動資訊，修改前查官方文件；模型偏好仍以使用者最新指示為準。
- 不要啟動另一個真實 bot 程序來做自動測試，以免實際推播或競態。自動測試使用暫存 DB 和 mock；公開來源讀取與最小模型 smoke 可分開驗證。
- 熱重載測試在獨立子程序執行，先替換資料庫路徑再載入 Cogs；重載會替換模組物件，不能讓其他測試持有舊 class／module globals 而使 mock 失效。

## 啟動與驗證

```bash
python -m pip install -r requirements.txt
python -m compileall -q bot.py config.py cogs services tests
python -m unittest discover -s tests -v
python bot.py
```

必要機密：`DISCORD_TOKEN`、`GEMINI_API_KEY`。缺少 Gemini key 時其餘功能仍載入；缺少 Discord token 時主程式提示後退出。

部署第一次需要完整重啟（更新了 bot.py／載入模組）；Cog 後續修改可 `/reload extension:ai_chat`、`youtube`、`admin`、`community`。套件／.env／主程式修改需重啟。注意備份資料庫並保持單一程序。

2026-10-01 版本 2026.10.01.1：41 項自動測試通過，含活動、競爭名額、部分通知恢復、大型揪團標記、權限、啟動／熱重載及 session 關閉。先前公開來源實測 15 支影片、10 則貼文；真實 3.8 Flash 最小請求成功。自動測試沒有向真實 Discord 建立活動，按鈕仍需上線人工驗證。

## GCP 維運資訊

- Project：`makecom-459608`；VM：`instance-20260331-134501`；zone：`us-central1-a`。
- 執行使用者：`j007sgps`；專案：`/home/j007sgps/MyFirstDiscordBot`；Python：`venv/bin/python`。
- 目前使用 nohup，更新前以精確 argv／cwd／uid 確認 bot PID，停止旧程序後才啟動新程序，禁止廣泛 pkill 或同時跑兩個 bot。
- `.env`、兩個 DB、`settings.json`、`personas.json`、`shachiku.md` 先備份到 `/home/j007sgps/vibe-backups/<時間>`；SQLite 使用 backup API，備份機密檔案權限 600、目錄 700。
- 遠端 settings／personas 可由 Discord 修改，git pull 不可覆蓋這些執行時資料。2026-10-01 部署前 AI 搜尋已由使用者開啟，勿用本機預設關閉值覆蓋。
- 更新使用 ff-only，驗證預期 commit、測試、Slash 同步與單一程序。更新失敗先停新程序，再恢復舊碼、依賴及必要備份後啟動。

PowerShell 管線若 stdout 無法編碼繁中，可設 `$env:PYTHONIOENCODING='utf-8'` 再執行檢查；這與來源 UTF-8 檔案是否損壞無關。
