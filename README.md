# 動畫迷因資料庫

本專案是本地優先的動畫梗圖整理工具。資料流程是 DiscordChatExporter fixed JSON 匯入 SQLite，下載本地附件，產生縮圖，使用 OCR 輔助校驗，最後透過本地 Web UI 快速瀏覽、搜尋、編輯、收藏與清理資料。

## 目前能力

- 匯入 DiscordChatExporter 清理後的 fixed JSON 到 `data/anime_memes.sqlite3`
- 下載 Discord 附件到 `data/attachments/`
- 產生 Web UI 用縮圖到 `data/thumbnails/`
- PaddleOCR / EasyOCR 輔助辨識，GIF / MP4 / MOV / WEBM / M4V 不做 OCR
- taxonomy v2 規則標籤、人工標籤、Agent／本地 LLM 候選標籤與人工審核
- Notion 匯入用 CSV 匯出
- 本地 Flask Web UI：搜尋、篩選、瀏覽、編輯欄位、編輯人工標籤、收藏、狀態切換、永久刪除

目前使用 **SQLite＋Flask**。後端支援梗圖名稱、替代搜尋詞、繁簡／標點正規化、多詞跨欄位搜尋與相關度排序，標籤亦可獨立精確篩選。新欄位可透過本機 API 編輯，UI 表單尚待整合；既有 UI 仍明確指定更新排序。

## 發展方向與文件

後端已拆出本機管理、公開唯讀搜尋、發布工具與私人選用 MCP。公開版由 Nginx 供應縮圖與轉送 API，SQLite／媒體以容器外目錄掛載；公開資料可驗證後切換版本。私人管理頁可選圖、檢查、匯出及啟用版本，公開搜尋頁支援預覽、分享與取圖。

操作與 API 契約見 [雙版本後端與發布操作](docs/backend-refactor.md)。公開版不提供 Agent 專用接口或 MCP；私人管理版保留既有 UI，另可啟用唯讀 MCP。公開與私人入口不可混用，私人管理端預設只綁 localhost。

| 入口 | 內容 |
|---|---|
| [文件索引](docs/README.md) | 現行規格、操作手冊與歷史文件的閱讀入口 |
| [整體規格](docs/personal-meme-platform-spec.md) | UI、API、部署、資源限制及分階段驗收 |
| [DB 與搜尋規格](docs/database-search-spec.md) | 新欄位、替代詞、搜尋規則與資料遷移 |
| [Agent 標籤操作手冊](docs/agent-image-tagging-guide.md) | 已有的圖片標籤匯出、匯入與審核流程 |
| [歷史區](docs/archive/README.md) | 階段設計、工作紀錄、維護與背景研究 |

## 重要資料位置

- 主資料庫：`data/anime_memes.sqlite3`
- 資料庫備份：`data/backups/`（清理曾保留 3 份；後續 migration 另新增備份，不自動淘汰）
- 歷史檢查紀錄：`data/archive/`
- 原始資料保存：`data/original_data/`
- 完整附件：`data/attachments/`
- 縮圖：`data/thumbnails/`
- Notion CSV：`exports/notion/`
- 人工 review CSV：`exports/review/`
- PaddleOCR 模型：`models/`

`data/`、`exports/`、`models/`、`.env`、`.venv/` 都應維持不提交到 Git。

2026-11-24 已整理 data：原圖與縮圖全數保留，raw／fixed 增量匯出保留最新 3 組；初始完整原始資料另行保留，不按增量批次刪除。舊備份、測試樣本、零長度日誌與臨時依賴驗證環境已清除。本次為一次性整理，尚未加入自動保留政策；舊 export_runs 的歷史路徑可能指向已清理檔案，最近成功同步來源仍保留。本機繼續使用完整 `.venv`，`exports/` 未清理。

## 安裝依賴

統一使用複數 requirements 命名；既有 `.venv` 保留完整本機環境。全新本機管理環境安裝：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-admin.txt
```

- `requirements.txt`：公開服務基礎（Flask、Werkzeug、Waitress、OpenCC），不含 Pillow／OCR／MCP。
- `requirements-admin.txt`：基礎＋Pillow，供完整管理與發布。
- `requirements-ocr.txt`：管理＋既有 PaddleOCR GPU，僅本機資料準備時選裝，需相容 Python／CUDA。
- `requirements-mcp.txt`：基礎＋官方 MCP SDK，私人 MCP 選裝。

OCR 與 MCP 不需日常使用時不必安裝；EasyOCR 僅保留呼叫入口，未納入預設清單。清單是直接依賴，尚非完整 lockfile。所有 Docker target 都不安裝 OCR，公開 target 也不含管理原始碼與圖片處理。

本機 Docker 的[私人管理頁](http://localhost:3636)（`/browse` 為本機簡潔瀏覽）與[模擬公開頁](http://localhost:3637)使用獨立入口；不走 Docker 的 `web` 指令預設在 3638。首次啟動、隔離資料與換版驗收見[本機操作說明](docs/backend-refactor.md#dockernginx-本機啟動)。

## 啟動 Web UI

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

預設網址：

```text
http://127.0.0.1:3638
```

Web UI 預設顯示 `ready` 資料，可切換 `ignored` 或 `all`。點卡片會開啟 detail drawer，可以修改作品名稱、說話者、圖片台詞、狀態、備註與人工標籤。側欄的「手動新增梗圖」可上傳 JPG、PNG、WebP 或 GIF（上限 50 MB），並直接寫入本地附件、縮圖與 SQLite；這類資料不會影響 Discord 增量同步游標。

永久刪除會移除該筆 `meme_entries`、人工/自動標籤關聯、收藏、review 紀錄。如果該 attachment 沒有被其他 entry 共用，也會刪除 `attachments` 資料列，以及本地原圖和縮圖。這個操作不進回收區。

## 常用 CLI

查看指令：

```bat
.venv\Scripts\python.exe -m src.anime_memes --help
```

一鍵增量同步 Discord（自動備份、重疊抓取、匯入、附件與縮圖，並回報特殊案例）：

```bat
src\commands\sync_discord.cmd
```

輸出狀態分為 `initial_import`、`updated`、`no_changes`、`partial`。`special_cases` 會列出被編輯、疑似刪除、無法配對文字或單則多附件的訊息；這些案例不會自動刪除或覆蓋人工修正，需人工確認。

同步會在資料庫初始化前使用 SQLite backup API 備份，包含已提交的 WAL 資料。附件或縮圖失敗時輸出 `partial`、exit code 2，已匯入內容保留，但成功游標不前進；修復問題後重跑相同命令，會重試先前失敗的附件。明確使用 `--skip-attachments`／`--skip-thumbnails` 時，該階段不列入完成判斷。

若匯出程式回報 `Authentication token is invalid`，請在本機 `.env` 更新有效的 `DISCORD_BOT_TOKEN` 後重試。此時還沒有取得新訊息，不能視為「沒有新資料」。不要把 token 貼到對話或版本控制；CLI 錯誤訊息會遮蔽 token。

每次成功同步都會將本次抓取範圍、第一／最後訊息時間與 ID、實際新增／變更筆數寫入 `export_runs`。每個 Discord 頻道目前成功覆蓋到的時間、最後訊息及下次重疊起點則保存在 `discord_sync_state`；游標只會在完整同步成功後前進，空資料區間也會留下成功紀錄。

查看目前游標，不連線 Discord：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 sync-status
```

只匯入既有 fixed JSON：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 ingest data\fixed_exports\latest.fixed.json
```

下載附件：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 download-attachments --all --retry-failed
```

建立縮圖：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 build-thumbnails --missing
```

OCR review：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 ocr-review --engine paddle --device gpu --download-missing --retry-failed-downloads --order newest
```

匯出人工校驗 CSV：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-review --output exports\review\needs_review.csv
```

匯回人工校驗 CSV：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 import-review-corrections exports\review\needs_review.csv --mark-ready
```

Review／Notion CSV 會在公式開頭的文字前加入單引號，避免試算表將文字當公式。Review CSV 的 `_text_encoding` 欄請保留，回匯時會還原原始欄位的保護前綴；人工「修正」欄仍按輸入原文處理。Notion 匯出沒有對應解碼流程，因此可能看得到保護用的單引號。

永久刪除會保留附件 ID 的刪除紀錄，重新匯入同一份 Discord 匯出也不會重建該筆資料。無效或超過 Pillow 安全限制的圖片會標記為 `invalid_image`，後續下載／縮圖批次會跳過；需要重新下載驗證時可使用 `download-attachments --all --force`。HTML／SVG 等附件只提供下載，不在本地編輯器來源內執行。

前端狀態回歸測試可執行 `node --test tests/test_web_state.cjs`，涵蓋收藏草稿保留、請求順序與分頁重試。

匯出未標籤圖片給 Codex／Antigravity 等可讀圖 Agent：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-tagging-jobs --actor codex --untagged --limit 10 --output exports\tagging\jobs.jsonl
```

Agent 逐張讀圖並建立 `results.jsonl` 後，匯入待審核候選：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 import-tagging-results exports\tagging\results.jsonl
```

候選不會直接成為正式標籤；請在 Web UI 項目明細接受或拒絕。完整格式與安全規則見 [`docs/agent-image-tagging-guide.md`](docs/agent-image-tagging-guide.md)。

圖片匯出會實際解碼，損壞圖片計入 `skipped_invalid`。人工標籤欄位只編輯人工來源；規則及已接受的候選標籤顯示於圖片下方。接受／拒絕候選會保留尚未儲存的欄位內容。taxonomy 在 Web App 啟動時同步，修改設定後須重啟服務。

舊 `import-llm-tags` 已 deprecated，請改用上述工作匯出／結果匯入流程。舊入口仍會驗證名稱、數量、信心度與理由；錯誤行不會留下部分候選，有錯誤時 exit code 為 2。

匯出 Notion CSV：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-notion
```

## 批次發布

用 `publication bulk` 批次選取要發布的項目（也可用 `POST /api/publication/bulk`）。發布必須透過此指令或 API，不要直接改資料庫，因為每個發布項目都需要 public ID。

```bat
rem 先預覽，再發布所有 ready 項目
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 publication bulk --selected yes --dry-run
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 publication bulk --selected yes
```

可用 `--keys`／`--keys-file` 指定項目，或用 `--status`、`--tag`、`--q`、`--favorite`、`--no-tags`、`--published` 篩選；單次上限 5000 筆。

## 圖片尺寸與 R2

- 原圖：只存在本機（`data/attachments`），不公開。
- 480P 縮圖（長邊 ≤854、短邊 ≤480，WebP）：本機縮圖與 VPS 發布包使用；匯出時由本地原圖重新產生，無原圖則退回既有縮圖。
- 720P 大圖（長邊 ≤1280、短邊 ≤720，WebP，存於 `data/web`）：上傳 R2 作為公開「原圖」。

```bat
src\commands\build_media.cmd --kind all --missing
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 build-media --kind thumb|web|all [--all-entries] [--missing] [--force]
```

`--kind` 選擇縮圖、大圖或兩者；縮圖一律處理全部附件，720P 大圖預設只處理發布選取的項目，`--all-entries` 改為全部；`--missing` 只補缺少的檔案；`--force` 強制重新產生。

設定好 `MEMES_R2_*`（見 `.env.example`）後，`publication export` 會自動為已選取、且未手動填寫公開原圖網址的項目產生 720P 並上傳 R2（以內容雜湊命名，已存在則略過）；任一上傳失敗則不產生新版本。手動網址優先，其次 R2，皆無則僅發布縮圖。未設定 R2 時行為不變。

## 徹底撤回

取消公開並啟用新版後，被撤回的圖仍可能留在舊版本（可用 `?version=` 指定）、舊縮圖與 R2 上。在管理頁「發布管理 → 徹底清除舊版本與已撤回的圖片」先預覽再確認，或使用：

```bat
.venv\Scripts\python.exe -m src.anime_memes publication purge --dry-run
.venv\Scripts\python.exe -m src.anime_memes publication purge
```

- 只保留目前啟用版本（`--keep-recent N` 可多留最近 N 個）；刪除後無法退回舊版本。
- 縮圖只刪不再被保留版本使用的檔案。
- R2 只刪本程式上傳並記錄在資料庫、位於設定前綴底下、沒有被保留版本引用、也沒有仍勾選公開的項目使用的物件；不會列出或刪除 bucket 裡的其他檔案。內容相同而共用物件的圖，只要有一張仍需要就保留。
- Cloudflare 自訂網域有快取時，刪除的 R2 物件仍可能從邊緣快取提供，需依輸出的網址到後台清除快取；修改 R2 CORS 規則後同樣要清除快取才會生效。
- VPS 上的發布目錄是另一份副本，清理本機不會影響 VPS，需在 VPS 另外處理。

## 專案結構

- `src/anime_memes/`：主要 Python 程式
- `src/anime_memes/web.py`：Flask Web App 與 API
- `src/anime_memes/web_templates/`：Web UI HTML
- `src/anime_memes/web_static/`：Web UI CSS / JS
- `src/commands/`：Windows `.cmd` wrapper，主要給 LLM 或必要時人工快速呼叫
- `config/tags.taxonomy.json`：版本化標籤定義、規則與模型選取限制
- `docs/`：現行規格、Agent 操作手冊與文件索引
- `docs/archive/`：歷史階段規格、工作紀錄與背景研究
- `examples/`：Agent／本地 LLM JSONL 介面範例
- `tests/`：單元測試

## 測試

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m compileall src tests
```
