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

目前使用 **SQLite＋Flask**，搜尋為作品、說話者、台詞與 OCR 的文字包含比對，標籤另外篩選。

## 發展方向與文件

目標為「本機管理與整理、公開端唯讀搜尋、R2 直接取圖」，並提供受控的 Agent 搜尋接口。梗圖名稱、替代搜尋詞、相關度排序、公開部署及 MCP 都仍屬規劃，尚未因規格建立而實作；目前服務沒有完整公開存取控制，不應直接公開管理接口。

| 入口 | 內容 |
|---|---|
| [文件索引](docs/README.md) | 現行規格、操作手冊與歷史文件的閱讀入口 |
| [整體規格](docs/personal-meme-platform-spec.md) | UI、API、部署、資源限制及分階段驗收 |
| [DB 與搜尋規格](docs/database-search-spec.md) | 新欄位、替代詞、搜尋規則與資料遷移 |
| [Agent 標籤操作手冊](docs/agent-image-tagging-guide.md) | 已有的圖片標籤匯出、匯入與審核流程 |
| [歷史區](docs/archive/README.md) | 階段設計、工作紀錄、維護與背景研究 |

## 重要資料位置

- 主資料庫：`data/anime_memes.sqlite3`
- V1 備份：`data/anime_memes_v1.sqlite3`
- 原始資料保存：`data/original_data/`
- 完整附件：`data/attachments/`
- 縮圖：`data/thumbnails/`
- Notion CSV：`exports/notion/`
- 人工 review CSV：`exports/review/`
- PaddleOCR 模型：`models/`

`data/`、`exports/`、`models/`、`.env`、`.venv/` 都應維持不提交到 Git。

## 啟動 Web UI

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

預設網址：

```text
http://127.0.0.1:8765
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
