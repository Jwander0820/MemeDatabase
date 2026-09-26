# 視覺 LLM／Agent 自動標籤階段規格

- 文件狀態：已定案，待實作
- 規格版本：1.1
- 建立日期：2026-07-13
- 適用專案：AnimeMemes

## 1. 本階段目標

建立一套「模型無關、可審核、可追溯」的圖片標籤管線，讓本地 Vision LLM、Codex、Antigravity 或其他可讀取本地圖片的 Agent，都能透過相同工作格式完成以下流程：

1. 從 SQLite 取得待處理項目與本地圖片路徑。
2. 依版本化標籤目錄讀取圖片並提出結構化標籤建議。
3. 經過格式、標籤 ID、信心度與資料關聯驗證後寫入候選區。
4. 由人工接受或拒絕候選；只有接受的結果會成為正式標籤。
5. 保存執行者、模型、標籤目錄版本、理由及審核結果，供後續追蹤與重跑。

本階段的完成定義是：至少能以一個具備圖片讀取能力的 Agent，針對小批次本地圖片走完「匯出工作 → 讀圖 → 產生結果 → 匯入候選 → 人工審核 → 正式標籤」的端到端流程。

## 2. 範圍與原則

### 2.1 本階段包含

- 修復既有標籤設定檔的中文字碼，建立版本化 taxonomy v2。
- 保留並遷移既有正式標籤及來源資料。
- 建立標籤執行批次、候選結果與審核狀態的資料模型。
- 提供 JSONL 工作匯出及結果匯入 CLI。
- 在 Web UI 的項目明細中提供候選標籤接受／拒絕操作。
- 提供 Codex／Antigravity 共用的 Agent 操作手冊與範例。
- 建立核心標籤詞表，足以進行端到端驗證。

### 2.2 本階段不包含

- 不直接串接 Ollama、LM Studio 或雲端模型 API；本地 LLM adapter 留待下一階段。
- 不允許 Agent 任意執行 SQLite 寫入。
- 不讓模型自行建立正式標籤。
- 不自動接受高信心候選；所有模型／Agent 結果都先進入候選區。
- 不一次完成所有動畫迷因標籤詞彙；先建立可擴充結構與核心詞表。
- 不改變 Discord 同步、OCR、Notion 匯出與人工標籤原有語意。

### 2.3 設計原則

- **Provider-neutral**：核心管線不依賴特定模型或 Agent。
- **Config-first**：模型只能選擇 taxonomy 中已啟用且允許模型使用的標籤 ID。
- **Review-first**：模型輸出永遠是候選，人工決定是否成為正式標籤。
- **Stable ID**：程式與資料庫使用固定 `tag_id`，中文名稱只作顯示用途。
- **Auditable**：每一筆候選都能追溯到執行批次、模型、設定版本與理由。
- **Idempotent**：相同批次與相同結果重複匯入，不得產生重複候選或正式標籤。

## 3. 現況基線

截至 2026-07-13，本地資料庫共有 984 筆 meme entry 與 984 筆本地圖片；其中 899 筆完全沒有標籤。現有 106 個標籤關聯由 `auto` 及 `manual` 產生，尚無 `local_llm` 結果。

現有程式已具備：

- `import-llm-tags` JSONL 匯入入口。
- `entry_tags` 的來源、信心度與理由欄位。
- 關鍵字規則標籤及人工標籤。
- 本地附件、縮圖、Web UI 與單筆標籤編輯 API。

現有缺口：

- `import-llm-tags` 直接寫入正式標籤，且來源固定為 `local_llm`。
- 沒有待處理工作匯出、執行批次、候選／審核生命週期。
- 無法區分 Agent、provider、模型、prompt 或 taxonomy 版本。
- 模型可以輸入任意字串，沒有受控詞表驗證。
- Web 標籤 API 固定寫成 `manual`，不適合作為 Agent 寫入入口。
- `config/tags.rules.json`、範例與部分文件存在中文字碼亂碼；SQLite 中既有中文標籤仍正常。

## 4. 目標架構與資料流

```text
SQLite + 本地圖片 + taxonomy v2
              │
              ▼
      export-tagging-jobs
              │
              ▼
   jobs.jsonl + agent 操作手冊
              │
      ┌───────┴────────┐
      ▼                ▼
 Codex/Antigravity   未來本地 LLM adapter
      │                │
      └───────┬────────┘
              ▼
        results.jsonl
              │
              ▼
    import-tagging-results
              │
              ▼
       待審核候選標籤
              │
       Web UI 接受／拒絕
              │
              ▼
          正式標籤
```

Agent 只讀取工作檔、taxonomy 與圖片，並輸出結果檔；所有資料庫異動由專案 CLI 或 Web API 執行。

## 5. Taxonomy v2

### 5.1 設定檔與相容策略

- 新增 `config/tags.taxonomy.json`，作為標籤定義的唯一來源。
- 保留 `config/tags.rules.json` 一個相容版本週期；既有 ingest 改由 taxonomy 的 `text_keywords` 產生規則標籤。
- taxonomy 載入失敗、版本不支援、ID 重複或 category 不存在時，命令必須失敗，不得退回任意標籤模式。
- 所有專案文件、JSON 與 JSONL 統一使用 UTF-8；加入自動測試確保核心中文標籤可正確 round-trip。

### 5.2 根物件格式

```json
{
  "version": 2,
  "locale": "zh-TW",
  "policy": {
    "max_tags_per_entry": 6,
    "minimum_confidence": 0.65,
    "allow_unknown_tags": false
  },
  "categories": [],
  "tags": []
}
```

`policy` 規則：

- `max_tags_per_entry`：單一圖片最多六個候選；超出時整筆結果拒收並記錄錯誤。
- `minimum_confidence`：低於 0.65 的候選不匯入，列入匯入摘要的 `below_threshold`。
- `allow_unknown_tags`：本階段固定為 `false`。
- 信心度必須是 `0.0` 至 `1.0` 的有限數值。

### 5.3 Category 格式

```json
{
  "id": "reaction",
  "label": "反應／情緒",
  "description": "角色呈現的主要反應或情緒",
  "max_per_entry": 2
}
```

第一版固定分類：

- `reaction`：反應／情緒。
- `situation`：情境／主題。
- `intent`：台詞用途／溝通意圖。
- `visual`：可從畫面辨識的表現形式。
- `content`：內容提示。
- `legacy`：只為保留既有資料、不再建議使用的標籤。

### 5.4 Tag 格式

```json
{
  "id": "reaction_roast",
  "label": "嗆人",
  "category": "reaction",
  "description": "帶有嘲諷、回嘴、辱罵或尖銳吐槽的反應",
  "aliases": ["嘴人", "毒舌"],
  "text_keywords": ["白癡", "沒資格"],
  "visual_cues": ["憤怒表情", "指責姿勢"],
  "positive_examples": ["台詞直接貶低或反擊對方"],
  "negative_examples": ["只有生氣但沒有針對對方"],
  "enabled": true,
  "model_selectable": true
}
```

欄位規則：

- `id` 使用小寫 ASCII snake_case，發布後不可更改或重複使用。
- `label` 是 UI 顯示名稱；可改名但同一版本內不得重複。
- `category` 必須指向已存在的 category。
- `description` 必填，必須說明何時使用此標籤。
- `aliases` 用於搜尋與舊名稱對應，不直接成為正式標籤。
- `text_keywords` 供既有規則引擎使用；命中時來源仍為 `rule`，不是模型候選。
- `visual_cues`、正例與反例供 Agent／模型判斷。
- `enabled=false` 代表保留歷史資料但停止新增。
- `model_selectable=false` 代表規則或人工可用，但不得由模型選取。

### 5.5 第一版核心詞表

第一版至少包含下列 ID；實作時需為每一項補齊描述、正例與反例：

| Category | Tag ID | 顯示名稱 |
|---|---|---|
| reaction | `reaction_roast` | 嗆人 |
| reaction | `reaction_speechless` | 無言 |
| reaction | `reaction_shocked` | 震驚 |
| reaction | `reaction_angry` | 生氣 |
| reaction | `reaction_embarrassed` | 害羞 |
| reaction | `reaction_crying` | 哭泣 |
| reaction | `reaction_happy` | 開心 |
| reaction | `reaction_breakdown` | 崩潰 |
| situation | `situation_overtime` | 加班 |
| situation | `situation_stay_up_late` | 熬夜 |
| situation | `situation_romance` | 戀愛 |
| situation | `situation_school` | 課業 |
| situation | `situation_work` | 工作 |
| situation | `situation_social_death` | 社死 |
| intent | `intent_comment` | 吐槽 |
| intent | `intent_reject` | 拒絕 |
| intent | `intent_question` | 質問 |
| intent | `intent_agree` | 贊同 |
| intent | `intent_comfort` | 安慰 |
| intent | `intent_mock` | 嘲諷 |
| visual | `visual_close_up` | 表情特寫 |
| visual | `visual_multi_person` | 多人對話 |
| visual | `visual_text_heavy` | 文字密集 |
| visual | `visual_exaggerated_expression` | 誇張表情 |
| content | `content_sexual_innuendo` | 色情暗示 |
| content | `content_violence` | 暴力 |
| content | `content_profanity` | 髒話 |

既有標籤遷移：`加班`、`嗆人`、`熬夜` 分別映射至上述穩定 ID；`變態` 以 `content_perverted` 保留並允許人工使用；`梗圖` 以 `legacy_meme` 保留，設定 `enabled=false`、`model_selectable=false`，因所有資料本身皆為梗圖，沒有篩選價值。

## 6. 資料庫設計

### 6.1 正式標籤

為降低主資料庫 migration 風險，本階段採加法式相容 schema：保留既有 `tags.name` 主鍵與 `entry_tags.tag_name/source`，新增穩定 ID 與來源欄位。新程式一律使用 `tag_id`；舊欄位只作既有程式與外部查詢的一個版本相容層。

`tags` 新增：

- `id TEXT UNIQUE`，作為新接口使用的穩定 ID。
- `name TEXT PRIMARY KEY`，保留作顯示 label 與舊接口相容。
- `category TEXT NOT NULL`
- `description TEXT NOT NULL`
- `enabled INTEGER NOT NULL`
- `model_selectable INTEGER NOT NULL`
- `taxonomy_version INTEGER NOT NULL`
- `created_at`、`updated_at`

`entry_tags` 新增：

- `entry_key`：關聯 meme entry。
- `tag_id`：關聯 `tags.id`。
- `source_type`：`manual`、`rule` 或 `suggestion`。
- `source_ref`：規則名稱、審核後的 suggestion ID，人工標籤可為空。
- `confidence`、`reason`、`created_at`。
- 本階段保留既有唯一鍵 `(entry_key, tag_name, source)`；同一來源的相同標籤採冪等覆寫。未來若移除相容欄位，再改為以穩定 ID 組成唯一鍵。

同一 entry 可因不同來源擁有相同 tag；查詢及 UI 顯示時按 `tag_id` 去重。刪除人工標籤只能刪除 `source_type=manual`，不得移除規則或已接受候選的來源紀錄。

### 6.2 執行批次 `tagging_runs`

必要欄位：

- `id TEXT PRIMARY KEY`：UUID。
- `actor_type`：`agent` 或 `local_llm`；本階段只產生 `agent`。
- `actor_name`：例如 `codex`、`antigravity`。
- `model_name`：可空；已知時填入實際視覺模型名稱。
- `taxonomy_version`、`input_path`、`output_path`。
- `status`：`exported`、`importing`、`awaiting_review`、`completed`、`failed`。
- `exported_count`、`processed_count`、`suggestion_count`、`error_count`。
- `created_at`、`imported_at`、`completed_at`。

### 6.3 候選標籤 `tag_suggestions`

必要欄位：

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `run_id`、`entry_key`、`tag_id`
- `confidence REAL NOT NULL`
- `reason TEXT NOT NULL`
- `status`：`pending`、`accepted`、`rejected`
- `reviewed_by`、`reviewed_at`、`created_at`
- 唯一鍵為 `(run_id, entry_key, tag_id)`。

接受候選時，在同一 transaction 中把狀態改為 `accepted`，並新增 `entry_tags(source_type='suggestion', source_ref=<suggestion id>)`。拒絕只更新候選狀態。重複接受必須是無副作用的成功操作。

### 6.4 Migration

Migration 以 `ALTER TABLE` 加欄位及 `CREATE TABLE/INDEX` 建立新結構，並依現有中文 `tag_name` 對照 taxonomy 的 `label/aliases` 取得 `tag_id`。無法映射的既有標籤不得丟棄：以 `legacy_<sha1>` 建立不可供模型選取的 legacy 定義並保留原名稱。Migration 前先備份主庫；前後 `meme_entries` 與 `entry_tags` 筆數必須一致，否則停止後續工作並以備份還原。

2026-09-24 補充：顯示名稱可變更或交換，穩定 ID 與候選關聯必須保留。Alias 正規化為 canonical label；同一 entry、同一來源同時存在 alias 與 canonical label 時，合併成一筆並保留 canonical 資料。只有這種已驗證的重複關聯允許使 `entry_tags` 筆數下降，不同來源不得合併。未知舊標籤停用；明確人工建立的自訂標籤保留可用狀態。

## 7. CLI 與檔案協定

### 7.1 匯出工作

```powershell
python -m src.anime_memes --db data/anime_memes.sqlite3 `
  export-tagging-jobs `
  --actor codex `
  --untagged `
  --limit 50 `
  --output exports/tagging/jobs.jsonl
```

行為：

- 預設只選 `status=ready`、有可讀取本地圖片、沒有正式標籤且沒有同 taxonomy 版本 pending 候選的項目。
- `--untagged` 明確限制為沒有任何正式標籤；未指定其他選取條件時仍採此預設。
- 第一版支援 JPG、JPEG、PNG、WebP；GIF／影片及不存在的路徑跳過並計入摘要。
- `--limit` 必須為正整數；預設 50，最大 500。
- 建立 `tagging_runs` 後，以 JSON 摘要輸出 `run_id`、工作數、跳過數及檔案位置。
- JSONL 的 `image_path` 必須輸出正規化絕對路徑，方便不同 Agent 直接讀取。

每行工作格式：

```json
{
  "schema_version": 1,
  "run_id": "uuid",
  "taxonomy_version": 2,
  "key": "meme_1393132585340633221",
  "image_path": "D:/MainProject/AnimeMemes/data/attachments/1393132585340633221.jpg",
  "anime_title": "輝夜姬讓人想告白",
  "speaker": "伊井野",
  "quote_text": "你們這樣實在太色情了",
  "ocr_text": null,
  "existing_tags": []
}
```

### 7.2 Agent 結果

每個工作項目必須恰好輸出一行：

```json
{
  "schema_version": 1,
  "run_id": "uuid",
  "key": "meme_1393132585340633221",
  "image_summary": "角色以責備表情指責對方",
  "suggestions": [
    {
      "tag_id": "reaction_roast",
      "confidence": 0.86,
      "reason": "台詞及責備表情共同呈現直接嗆人的反應"
    }
  ]
}
```

若沒有適用標籤，輸出空 `suggestions`；不得省略該工作。`image_summary` 必填，但不直接寫入 meme entry 欄位。

### 7.3 匯入結果

```powershell
python -m src.anime_memes --db data/anime_memes.sqlite3 `
  import-tagging-results exports/tagging/results.jsonl
```

驗證順序：JSON/schema → run ID → entry key 屬於該批次 → taxonomy 版本 → tag ID → 數量限制 → confidence → reason。單行錯誤不阻止其他有效行匯入；命令最後輸出結構化摘要，並在有錯誤時以非零 exit code 結束。

摘要至少包含：`processed_entries`、`created_suggestions`、`empty_results`、`duplicates`、`below_threshold`、`errors` 及逐行錯誤訊息。相同檔案重複匯入只增加 `duplicates`，不得建立新候選。

舊的 `import-llm-tags` 在一個相容版本內保留，但標示 deprecated；它改為建立候選，不再直接建立正式標籤，並拒絕 taxonomy 以外的名稱。README 與 CLI help 必須指向新流程。

## 8. Web UI 與 API

項目 detail drawer 新增「AI／Agent 候選標籤」區塊，顯示標籤、category、信心度、理由、執行者與時間，並提供接受／拒絕操作。人工標籤編輯維持原介面。

新增 API：

- `GET /api/entries/<key>/tag-suggestions?status=pending`
- `POST /api/tag-suggestions/<id>/decision`

decision request：

```json
{
  "decision": "accepted",
  "reviewed_by": "local_user"
}
```

`decision` 僅允許 `accepted` 或 `rejected`。不存在回傳 404；狀態已決定且要求相同決定時回傳目前結果；若要求相反決定則回傳 409，本階段不提供反悔／撤銷功能。接受後，entry 的正式標籤清單立即出現該標籤。

## 9. Agent 操作手冊

新增 `docs/agent-image-tagging-guide.md`，Codex 與 Antigravity 共用同一份主流程，僅在附錄說明各工具如何開啟本地圖片。手冊必須要求 Agent：

1. 先讀 taxonomy 與 jobs JSONL。
2. 逐張實際讀取圖片，不得只依台詞猜測。
3. 同時參考圖片、作品名、說話者、台詞與 OCR，但以圖片內容作必要證據。
4. 只能使用 `model_selectable=true` 的標籤 ID。
5. 遵守 category 與總數上限；不確定時省略，不創造新標籤。
6. 每個工作輸出一行結果，理由需指出可觀察到的證據。
7. 只產生結果檔，透過 CLI 匯入，不直接操作 SQLite。
8. 匯入後回報處理、空結果、候選、重複及錯誤數。

手冊附一份 3 筆以內的測試工作與預期格式，供 Agent 在正式批次前 smoke test。

## 10. 錯誤、安全與重跑策略

- 圖片不存在或無法解碼：匯出時跳過；不得建立空候選冒充已處理。
- taxonomy 版本不一致：整份結果停止匯入，避免以舊詞表寫入新資料。
- 個別 JSONL 行損壞：記錄行號並繼續其他行，最後回傳非零 exit code。
- 未知 entry、未知 tag、NaN／無限 confidence、空 reason：該行拒收。
- 模型回傳空 suggestions：視為成功處理但無適用標籤，寫入批次完成度，不建立候選。
- 重跑同一工作：建立新 run，保留舊 run 與審核紀錄；預設不匯出仍有 pending 候選的 entry。
- 模型與 Agent 不接收 Discord token、`.env` 內容或資料庫寫入權限。
- 所有寫入使用 parameterized SQL 與 transaction。

## 11. 測試與驗收

### 11.1 自動測試

- taxonomy v2 正常載入，拒絕重複 ID、無效 category、無效 policy 與亂碼核心標籤。
- v1 關鍵字規則遷移後仍能產生相同既有標籤。
- DB migration 前後正式標籤關聯數一致，未知舊標籤成為停用 legacy 定義。
- 工作匯出選取正確項目、產生絕對圖片路徑、遵守限制並跳過不支援格式。
- 結果匯入涵蓋有效、空結果、低信心、未知標籤、錯誤 run、重複與部分壞行。
- 接受候選會建立正式標籤；拒絕不建立；重複接受具冪等性；衝突決定回傳 409。
- 人工標籤更新不刪除 rule 或 suggestion 來源標籤。
- Web UI 能顯示候選及接受／拒絕後的最新狀態。
- 現有 pipeline、Web、OCR、Discord 同步與 Notion 匯出測試全部通過。

### 11.2 端到端驗收

使用測試資料庫或備份資料選取 10 張未標籤 JPG／PNG／WebP：

1. 匯出命令產生 10 個有效工作及一個 run。
2. Codex 或 Antigravity 實際讀取全部圖片並產生一行一結果。
3. 匯入後有效建議全部出現在 pending 候選，不直接改變正式標籤。
4. Web UI 接受一筆、拒絕一筆；接受者出現在正式標籤，拒絕者不出現。
5. 相同結果檔再次匯入不新增資料。
6. 執行完整單元測試與 `compileall` 均通過。

## 12. 階段完成條件

以下項目全部成立才算完成：

- taxonomy v2、核心詞表及舊資料遷移完成，無標籤關聯遺失。
- 新 CLI、JSONL schema、候選資料表及 Web 審核流程完成。
- Codex／Antigravity 共用操作手冊可讓 Agent 無額外口頭指示完成流程。
- 10 張圖片端到端驗收成功，重跑不重複。
- 自動測試與既有 regression tests 通過。
- README、AGENT.md、範例與工作紀錄同步更新，中文檔案皆為正確 UTF-8。

## 13. 下一階段候選

- 實作 Ollama 或 OpenAI-compatible Vision adapter。
- 依人工接受／拒絕資料調整 taxonomy 描述與門檻。
- 增加批次審核、反悔／撤銷及模型比較。
- 評估高信心自動接受，但必須先有足夠人工審核樣本與可量化準確率。
