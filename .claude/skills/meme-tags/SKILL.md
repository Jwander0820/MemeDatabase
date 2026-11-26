---
name: meme-tags
description: 查詢與維護動畫梗圖資料庫的標籤。替新蒐集的梗圖填 tags、替既有圖片產生 AI 候選標籤、判斷要沿用現有標籤或新增標籤、修改 config/tags.taxonomy.json 時使用。
---

# 梗圖標籤

## 標籤存在哪裡

標籤分成兩種，來源不同：

| 種類 | 維護位置 | 用途 |
|---|---|---|
| 正式標籤（taxonomy） | `config/tags.taxonomy.json` | 有 ID、分類、描述、別名與正反例。AI 標註只能選這裡 `enabled=true` 且 `model_selectable=true` 的 ID。 |
| 自由標籤 | 資料庫 `entry_tags`，沒有定義檔 | 人工或匯入時直接打的字，例如作品名、角色名、`cat`。管理頁顯示在「舊版保留」分類。 |

`config/tags.rules.json` 是自動規則（依台詞關鍵字打標），不是標籤清單。
不要直接改 SQLite；標籤只透過下面的指令、匯入流程或管理頁寫入。

## 第一步：取得目前的標籤清單

清單會隨館藏變動，每次工作前重新讀取，不要憑記憶或沿用舊清單。在專案根目錄執行（唯讀，不會修改資料庫）：

```powershell
.venv\Scripts\python.exe -m src.anime_memes list-tags --min-count 3
```

- 輸出依分類列出正式標籤（含別名、描述、使用則數），最後列出使用至少 N 則的自由標籤。
- 要程式處理時加 `--format json`；欄位有 `name`、`category`、`aliases`、`description`、`count`、`in_taxonomy`。
- 需要正式標籤的 `id`、關鍵字、畫面線索與正反例時，直接讀 `config/tags.taxonomy.json`。
- 已停用的分類標籤（如 `表情特寫`、`多人對話`、`文字密集`、`誇張表情`、`髒話`）不會出現在分類清單中，也**禁止選用**；`legacy_meme`（`梗圖`）雖在設定檔停用，但因館藏已有紀錄而仍列於分類外自由標籤中，僅供人工最終保底。

沒有專案存取權的 Agent（例如只有聊天介面）無法執行指令。由委託方先執行上面的指令，把輸出貼進委託提示詞。

## 情境 A：替新蒐集的梗圖填 tags

適用 `docs/agent-meme-collection.md` 的候選 JSONL。`tags` 是文字陣列，匯入後成為人工標籤。

1. **先對照清單，優先沿用正式名稱**：同義詞務必改寫為 `list-tags` 的正式名稱（例如想寫「毒舌」「嘴人」→ `嗆人`、「傻眼」→ `無言`、「憤怒」→ `生氣`、「同意」→ `贊同`、「流淚」→ `哭泣`、「驚訝」→ `震驚`）。`import-web-memes` 匯入時不會自動展開別名，直接寫別名會暫時在資料庫產生 `legacy_*` 標籤，所以必須在產出候選 JSONL 時就先正規化。
2. **每張圖選 1～6 個**，只填有畫面或台詞依據的詞，寧缺勿濫。建議組合：
   - 反應／情緒 1～2 個（例如 `生氣`、`無言`）
   - 用途或情境 1～2 個（例如 `吐槽`、`拒絕`、`加班`）
   - 畫面表現或內容提示 0～2 個（例如無台詞／字幕的反應圖可補 `無字反應圖`，有台詞則不可加；有疑慮時加上 `色情暗示`、`暴力`、`變態`）
   - 作品與主要角色 0～2 個：作品名與角色名（例如 `海綿寶寶`、`派大星`、`刃牙`）一律寫成自由標籤，不做成正式標籤。
3. **區分「標籤（tags）」與「台詞字詞／替代搜尋詞（search_aliases）」**：
   - 本系統搜尋（`src/anime_memes/search.py`）已同時索引 `display_title`、`quote_text`、`speaker`、`anime_title`、`tags` 與 `search_aliases`，並會自動去除標點與統一繁簡（例如台詞寫「復！活！」，搜尋「復活」本身就能命中，**不需為了讓該詞被搜到而再把台詞字眼加進 `tags`**）。
   - `tags` 應保留給**跨圖片共用的分類與側欄點選篩選詞**，不要把只出現一次的台詞字詞（例如「本人」「無所謂」「禮物」「星期天」）硬寫成自由標籤。
   - 若圖片需要額外的白話或聯想搜尋詞（沒寫在台詞與標題裡的說法），因 `import-web-memes` 目前尚不匯入 `search_aliases`，請先附在審核摘要或 `usage` 供人工入庫後填入管理頁的「也可以這樣搜」，不要以此造出一次性的自由標籤。
4. 任務指定的搜尋詞要保留。
5. **保底標籤**：真的難以歸類、但又一定要給標籤時，才用自由標籤 `搞笑`、`實用`、`梗圖`、`迷因`、`表情包`。能找到更具體的標籤就不要用這幾個。

### 同義詞與搜尋

正式標籤的別名目前只用於標籤輸入與資料庫整理，**搜尋還不會自動展開別名**：搜尋 `貓貓` 找不到只標了 `貓咪` 的圖。所以館藏裡同時有 `貓咪` 和 `cat` 這類同義標籤是刻意保留的，不要自行合併或刪除。

## 情境 B：替既有圖片產生 AI 候選標籤

依 `docs/agent-image-tagging-guide.md` 的流程（`export-tagging-jobs` → 逐張看圖 → `import-tagging-results`）。

- 只輸出 taxonomy 的 `tag_id`，不可用中文名稱，也不可自創 ID。
- 遵守各分類的 `max_per_entry` 與每則總數上限。
- 結果進入候選區，等人工審核後才成為正式標籤。

## 需要新標籤時

先確認現有標籤、別名，以及是否其實應放進 `display_title`／`quote_text`／`search_aliases`，確認真的需要共用分類標籤後再新增。判斷方式：

| 情況 | 做法 |
|---|---|
| 作品、主要角色、常見物件／特定梗，或只有少數圖會用到 | 作品／主要角色／常用物件寫成自由標籤，不改設定檔；僅屬單圖字眼或白話搜法改放 `display_title` 或交由人工填 `search_aliases`。 |
| 已有標籤的同義詞 | 不要新增，改用現有標籤。若這個說法常用，可加進該標籤的 `aliases`。注意：下次資料庫初始化（`init_db`，含啟動管理端）會把館藏中同名的自由標籤**全部改名**為正式名稱，加別名前先用 `list-tags` 看它目前被幾則使用，並告知使用者。 |
| 新的情緒、情境或用途，預期會反覆用到，且希望 AI 也能標 | 新增為正式標籤（見下方步驟）。 |
| 某個自由標籤已累積大量使用（`list-tags` 數字高） | 可提議升級成正式標籤；ID 另取，`label` 沿用原名稱，舊資料就會自動對上。 |

### 新增正式標籤的步驟

1. 在 `config/tags.taxonomy.json` 的 `tags` 陣列加一筆，格式照現有標籤，例如：

   ```json
   {
     "id": "situation_social_death",
     "label": "社死",
     "category": "situation",
     "description": "在他人面前極度尷尬或公開出糗",
     "aliases": ["公開處刑"],
     "text_keywords": ["丟臉", "好尷尬"],
     "visual_cues": ["群眾注視", "羞恥反應"],
     "positive_examples": ["角色在公眾場合明顯出糗"],
     "negative_examples": ["私下害羞"],
     "enabled": true,
     "model_selectable": true
   }
   ```

   規則：
   - `id` 為小寫英文、數字與底線，慣例是 `<分類>_<英文名>`，不可與既有 ID 重複。
   - `label` 不可與其他標籤或別名重複。
   - `category` 必須是 `categories` 裡既有的 ID：`reaction`（反應／情緒）、`situation`（情境）、`intent`（台詞用途）、`visual`（畫面表現）、`content`（內容提示）、`subject`（主角／物件）、`legacy`。
   - **別名不可與館藏中正在使用的任何標籤同名**，否則下次啟動管理端時那些標籤會被改名。新增前先確認：
     `.venv\Scripts\python.exe -m src.anime_memes list-tags --format json --min-count 1`，在輸出中搜尋要用的別名。
   - `label` 可以和既有的自由標籤同名，這就是把自由標籤升級成正式標籤的方式，舊資料會直接對上。
   - 需要先知道原作才能判斷的標籤（例如 `改字版`），設 `"model_selectable": false`，只給人工使用。
   - `description` 必填。五個陣列欄位都要存在；沒有內容就給空陣列，陣列裡不可有空字串。
2. 執行 `list-tags` 驗證。設定檔有錯時會直接報出是哪個欄位，新標籤應出現在對應分類下。
3. 執行 `.venv\Scripts\python.exe -m unittest discover -s tests`，確認測試仍通過。
4. 私人管理端的 Docker 映像內含 `config/`。改完要重建才會生效：

   ```powershell
   docker compose --profile private up -d --build --wait
   ```

5. 回報新增了哪些標籤與理由。設定檔的修改需經使用者確認，未經授權不要 commit。

### 停用標籤

不要刪除已使用過的正式標籤，改成 `"enabled": false`，讓既有資料仍可對應。只想讓 AI 不再選用，但人工仍可打的標籤，設 `"model_selectable": false`。

## 交付時回報

列出本次用到、但不在 `list-tags` 清單中的新標籤，各附一句理由（排除作品名與角色名）。人工審核時才能決定要保留、併入現有標籤、改列 `search_aliases`，或升級成正式標籤。
