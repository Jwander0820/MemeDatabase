# Agent 圖片標籤操作手冊

本手冊供 Codex、Antigravity 及其他能讀取本地圖片、讀寫 JSONL 並執行命令的 Agent 使用。Agent 不得直接修改 SQLite；所有結果必須經專案 CLI 驗證後進入候選區，再由人工審核。

## 前置條件

- 工作目錄是專案根目錄。
- `data/anime_memes.sqlite3` 與工作中的 `image_path` 僅供本機使用，不得上傳。
- 執行前先完整閱讀 `config/tags.taxonomy.json`。
- 只能輸出 `enabled=true` 且 `model_selectable=true` 的 `tag_id`。

## 標準流程

### 1. 匯出小批次工作

```powershell
.venv\Scripts\python.exe -m src.anime_memes `
  --db data\anime_memes.sqlite3 `
  export-tagging-jobs `
  --actor codex `
  --untagged `
  --limit 10 `
  --output exports\tagging\jobs.jsonl
```

Antigravity 將 `--actor` 改為 `antigravity`。若已知實際視覺模型，可加上 `--model <model-name>`。

### 2. 逐行處理工作

每行都是獨立工作，包含 `run_id`、`key`、圖片絕對路徑、作品、角色、台詞、OCR 與既有標籤。

對每一行：

1. 實際開啟並查看 `image_path`，不可只讀台詞猜測。
2. 綜合圖片、台詞與 metadata；圖片必須提供必要的視覺證據。
3. 依 taxonomy 的描述、visual cues、正例與反例選擇標籤。
4. 遵守每個 category 的 `max_per_entry` 及總數上限。
5. 不確定時省略標籤；不得創造新 ID 或改用中文 label。
6. 每個工作都輸出一行；沒有適用標籤時輸出空 `suggestions`。

### 3. 寫出結果 JSONL

```json
{"schema_version":1,"run_id":"原工作 run_id","key":"原工作 key","image_summary":"角色以憤怒表情指責對方","suggestions":[{"tag_id":"reaction_roast","confidence":0.86,"reason":"責備台詞與憤怒表情共同呈現直接回擊"}]}
```

要求：

- `image_summary` 必填，簡短描述實際看見的內容。
- `confidence` 是 0 至 1 的有限數值；低於 0.65 的結果不會建立候選。
- 工作匯出會解碼圖片；損壞檔案會跳過並列入 `skipped_invalid`，不占用工作數。
- `reason` 必須說明圖片或台詞中的可觀察證據。
- 同一個 `tag_id` 不得在一行重複。
- 不得漏掉工作；空結果仍需輸出該行。

### 4. 匯入候選

```powershell
.venv\Scripts\python.exe -m src.anime_memes `
  --db data\anime_memes.sqlite3 `
  import-tagging-results exports\tagging\results.jsonl
```

檢查摘要中的：

- `processed_entries`：成功處理的工作數。
- `created_suggestions`：建立的候選數。
- `empty_results`：沒有適用標籤的工作數。
- `duplicates`：重複匯入、未新增的候選數。
- `below_threshold`：低於最低信心度的候選數。
- `errors`：逐行錯誤；非空時命令 exit code 為 2。

### 5. 人工審核

啟動 Web UI，在圖片明細的「AI／Agent 候選標籤」區塊接受或拒絕。只有接受的候選會成為正式標籤。

```powershell
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

## Smoke test

正式批次前先用 `--limit 3`：

1. 確認三個 `image_path` 都可讀取。
2. 結果檔恰好三行且 JSON 可解析。
3. 匯入摘要沒有 errors。
4. Web UI 可看到候選，但正式標籤尚未改變。
5. 接受一筆、拒絕一筆，確認只有接受者出現在正式標籤。

## 禁止事項

- 不讀取或輸出 `.env`、Discord token、私有 CDN 資訊。
- 不直接執行 `INSERT`、`UPDATE`、`DELETE` 修改 SQLite。
- 不把本地圖片傳送至未經使用者授權的雲端服務。
- 不使用 taxonomy 外的標籤，也不因高信心度跳過人工審核。
