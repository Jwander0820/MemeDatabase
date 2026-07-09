# 動畫迷因資料庫

這是一個本地優先的動畫梗圖資料管線，用來把 Discord 頻道匯出的訊息與附件整理成可搜尋的 SQLite 資料庫，並輸出可匯入 Notion 的 CSV。

## V1 流程

1. 用 DiscordChatExporter 匯出 Discord 頻道資料。
2. 將 raw JSON 轉成固定格式 fixed JSON。
3. 匯入 SQLite，建立訊息、附件、梗圖 entry、標籤與待校驗項目。
4. 下載圖片附件到本地快取。
5. 用 PaddleOCR 驗證低信心資料，GIF 與影片不做 OCR。
6. 匯出 needs-review CSV 給人工修正。
7. 將人工修正 CSV 匯回 SQLite。
8. 匯出 Notion CSV。

## 重要資料位置

- 主資料庫：`data/anime_memes.sqlite3`
- V1 定版快照：`data/anime_memes_v1.sqlite3`
- 附件快取：`data/attachments/`
- Notion 匯出：`exports/notion/`
- 人工校驗 CSV：`exports/review/`
- PaddleOCR 模型快取：`models/`

以上都是本地資料，已由 `.gitignore` 排除，不建議上傳 GitHub。

## 程式結構

- `src/anime_memes/`：正式 Python 程式碼。
- `src/commands/`：Windows `.cmd` wrapper，給 LLM 或維護時快速調用，不是主要人工入口。
- `config/`：標籤規則。
- `examples/`：人工標籤與本地 LLM 建議格式範例。
- `tests/`：單元測試與 OCR smoke test。

一般使用與維護優先呼叫 Python CLI：

```bat
.venv\Scripts\python.exe -m src.anime_memes --help
```

## 安裝與環境

真實 Discord token 只放在 `.env`，不要提交到 repo。可從 `.env.example` 複製一份後填入本機設定。

OCR 依賴：

```bat
.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

`requirements-ocr.txt` 是 OCR 必要依賴；`requirement.txt` 是目前本機完整 freeze，主要用來記錄這台機器可跑 PaddleOCR GPU 的狀態。

## 常用指令

初始化資料庫：

```bat
.venv\Scripts\python.exe -m src.anime_memes init-db
```

將 raw DiscordChatExporter JSON 轉成 fixed JSON：

```bat
.venv\Scripts\python.exe -m src.anime_memes convert data\raw_exports\example.raw.json data\fixed_exports\example.fixed.json
```

匯入 fixed JSON：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 ingest data\fixed_exports\latest.fixed.json
```

針對低信心或待校驗項目跑 PaddleOCR GPU：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 ocr-review --engine paddle --device gpu --download-missing --retry-failed-downloads --order newest
```

匯出人工校驗 CSV：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-review --output exports\review\needs_review.csv
```

匯回人工校驗結果：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 import-review-corrections exports\review\needs_review.csv --mark-ready
```

匯出 Notion CSV：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-notion
```

使用 DiscordChatExporter 做增量匯出：

```bat
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 export-discord --incremental
```

如果想用短入口，可使用 `src\commands\` 內的 `.cmd` wrapper。這些 wrapper 會先切回專案根目錄，因此 `data/`、`exports/`、`.env` 等相對路徑仍以 repo root 為基準。

## 人工校驗 CSV

`export-review` 會輸出需要人工檢查的項目。可以直接編輯這些欄位：

- `修正作品名稱`
- `修正說話者`
- `修正圖片台詞`
- `修正標籤`
- `修正狀態`
- `修正備註`

匯入時若加上 `--mark-ready`，有填台詞但沒有填 `修正狀態` 的資料會自動標成 `ready`。如果某些列是無用資訊，可以從 CSV 刪掉；人工處理後剩下的未修項目可在資料庫中標成 `ignored`。

## OCR 行為

- PaddleOCR 可用 `--device gpu` 指定 GPU。
- PaddleOCR / PaddleX 模型快取會導向 `models/`。
- `.gif`、`.mp4`、`.mov`、`.webm`、`.m4v` 不做 OCR。
- OCR 結果會透過 OpenCC 轉成繁體後再寫入 `ocr_text`，相似度比對也會先做繁體化。
- OCR 只作為驗證信號，不會自動覆蓋 Discord 文字或人工修正。

## 標籤

規則標籤在：

```text
config/tags.rules.json
```

可用關鍵字觸發自動標籤。人工標籤、規則標籤、本地 LLM 建議標籤會用不同 `source` 存在 `entry_tags`，互不覆蓋。

本地 LLM JSONL 格式：

```json
{"key":"meme_...","suggested_tags":["加班"],"confidence":0.8,"reason":"short reason"}
```

## 測試

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe -m compileall src tests
```
