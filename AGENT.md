# AGENT.md

## 專案目標

建立本地優先的動畫梗圖資料庫。核心流程是：

1. DiscordChatExporter raw JSON 轉 fixed JSON
2. fixed JSON 匯入 SQLite
3. 下載完整附件並建立縮圖
4. OCR 輔助校驗低信心案例
5. Taxonomy 規則 / 人工 / Agent 候選標籤整理與審核
6. Web UI 本地瀏覽、搜尋、編輯與清理
7. 匯出 Notion CSV

V1 不上傳 R2、不直接呼叫 Notion API、不使用雲端 LLM 標籤。

## 資料安全邊界

- 不要提交 `.env` 或任何 Discord bot token。
- 不要提交 `data/`、`exports/`、`models/`、`.venv/`。
- `data/anime_memes.sqlite3` 是目前主資料庫。
- `data/anime_memes_v1.sqlite3` 是 V1 備份。
- Discord CDN URL 可能過期；長期保存以 `data/attachments/` 本地附件為準。
- OCR 是輔助校驗，不覆蓋 Discord 文字或人工修正。
- GIF / MP4 / MOV / WEBM / M4V 不做 OCR，也不產一般圖片縮圖。

## 程式架構

```text
src/anime_memes/
  cli.py          CLI 入口
  db.py           SQLite schema 與 migration
  attachments.py  Discord API refresh / attachment URL 輔助
  images.py       附件下載與縮圖產生
  web.py          Flask Web App 與 API
  web_templates/  Web UI HTML
  web_static/     Web UI CSS / JS
  ingest.py       fixed JSON 匯入、配對、OCR review 流程
  matching.py     文字與附件配對規則
  ocr.py          PaddleOCR / EasyOCR wrapper
  text.py         OpenCC 簡轉繁與文字處理
  tags.py         taxonomy、規則標籤、人工標籤與舊格式相容
  tagging.py      Agent 工作匯出、候選匯入與審核
  review.py       needs-review CSV 匯出與人工修正匯入
  notion.py       Notion-ready CSV 匯出

src/commands/
  *.cmd           Windows wrapper。主要給 LLM 或必要時人工快速呼叫。
```

## Web UI API

常用啟動：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

預設網址：

```text
http://127.0.0.1:8765
```

主要 API：

- `GET /api/entries`
- `GET /api/entries/<key>`
- `PATCH /api/entries/<key>`
- `PUT /api/entries/<key>/tags`
- `GET /api/entries/<key>/tag-suggestions`
- `POST /api/tag-suggestions/<id>/decision`
- `POST /api/entries/<key>/favorite`
- `DELETE /api/entries/<key>/favorite`
- `DELETE /api/entries/<key>`
- `GET /media/attachments/<attachment_id>`
- `GET /media/thumbnails/<attachment_id>`

永久刪除規則：

- 刪除 `meme_entries` 該筆資料。
- 刪除該 entry 的 `entry_tags`、`entry_favorites`、`review_items`。
- 如果 attachment 沒有被其他 entry 使用，刪除 `attachments` row。
- attachment 未被共用時，也刪除本地原圖與縮圖。
- 不刪 `discord_messages`，保留原始訊息紀錄。
- 檔案刪除只允許發生在設定的 attachments / thumbnails 目錄底下。

## 常用命令

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

匯出 / 匯入人工 review：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-review --output exports\review\needs_review.csv
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 import-review-corrections exports\review\needs_review.csv --mark-ready
```

Agent 圖片標籤工作：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-tagging-jobs --actor codex --untagged --limit 10 --output exports\tagging\jobs.jsonl
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 import-tagging-results exports\tagging\results.jsonl
```

操作前必須閱讀 `docs/agent-image-tagging-guide.md`。Agent 只能輸出結果 JSONL，不得直接修改 SQLite。

測試：

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m compileall src tests
```

## 後續方向

- R2 同步可使用 `attachments.r2_url` 欄位補上。
- GitHub Pages 只適合未來唯讀展示版；本地編輯仍需要 Flask 或其他後端。
- Agent／本地 LLM 標籤走受控 JSONL 候選介面，人工接受後才成為正式標籤。
