# 動畫迷因資料庫

本專案是本地優先的動畫梗圖整理工具。資料流程是 DiscordChatExporter fixed JSON 匯入 SQLite，下載本地附件，產生縮圖，使用 OCR 輔助校驗，最後透過本地 Web UI 快速瀏覽、搜尋、編輯、收藏與清理資料。

## 目前能力

- 匯入 DiscordChatExporter 清理後的 fixed JSON 到 `data/anime_memes.sqlite3`
- 下載 Discord 附件到 `data/attachments/`
- 產生 Web UI 用縮圖到 `data/thumbnails/`
- PaddleOCR / EasyOCR 輔助辨識，GIF / MP4 / MOV / WEBM / M4V 不做 OCR
- 規則標籤、人工標籤、本地 LLM 建議標籤匯入
- Notion 匯入用 CSV 匯出
- 本地 Flask Web UI：搜尋、篩選、瀏覽、編輯欄位、編輯人工標籤、收藏、狀態切換、永久刪除

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

輸出狀態分為 `initial_import`、`updated`、`no_changes`。`special_cases` 會列出被編輯、疑似刪除、無法配對文字或單則多附件的訊息；這些案例不會自動刪除或覆蓋人工修正，需人工確認。

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
- `config/`：規則設定
- `examples/`：本地 LLM 介面範例
- `tests/`：單元測試

## 測試

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m compileall src tests
```
