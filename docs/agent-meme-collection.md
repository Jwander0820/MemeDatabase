# 委託 Agent 蒐集新梗圖

選一個能搜尋網頁、開啟來源並看圖的 Agent，複製下方共用提示詞，填入本次的主題或來源網址。第一次可先收 10 張確認方向。續收時附上前次清單；多個 Agent 分工時各給一個主題，最後把清單一起交給整併用的 Agent。只有聊天介面的 Agent 可直接回傳 JSONL 文字。

## 共用提示詞

委託前在專案根目錄執行 `.venv\Scripts\python.exe -m src.anime_memes list-tags --min-count 3`，把輸出貼到提示詞的標籤清單位置。有專案存取權的 Agent 可改讀 `.claude/skills/meme-tags/SKILL.md`（或 `.agents/skills/meme-tags/SKILL.md`）與 `.agents/skills/review-meme-candidates/SKILL.md` 自行取得。

```text
請依【本次主題或來源網址】蒐集可用於聊天回覆的梗圖。逐一打開來源頁並看清實圖，記下能再次找到該圖的頁面連結。搜尋結果縮圖只作線索。看不到實圖的項目另列待查，並說明原因。

交付一份 UTF-8 JSONL 候選清單，每個不同版本一行。每行包含以下欄位（缺少時填 null，勿使用 character 欄位）：
- source_page：指向該張圖的貼文或頁面；沒有穩定來源頁填 null。
- image_url：可取得的圖片直連網址（JPG/PNG/WebP/GIF，上限 32 MiB），沒有直連或改用本機檔時填 null。
- local_path：本機圖片批次才填，為相對於 --local-root 且以 / 分隔的路徑；網路圖可省略或填 null。
- subject：作品或主題；不明填 null。
- speaker：說話者或主要出鏡角色；難辨或無單一角色時填 null，不要由標籤順序推定。
- display_title：梗圖名稱（選填）；無字圖或需要好記別稱時可給簡短標題，台詞清楚時可填 null。
- caption：逐字保留圖上台詞或字幕，保留標點與換行；無字圖固定填 null，模糊處不要猜。
- description：用一句繁中描述畫面上可辨認的動作、表情或物件。
- tags：每次打開實圖時就填好 1～6 個 AI 推薦標籤，供審核頁直接預覽與修改。保留任務指定的搜尋詞，再根據畫面、動作或台詞推薦情緒、用途與情境；無字圖也適用，有畫面依據時可補「無字反應圖」（有台詞則勿加）。優先沿用現有標籤清單中的正式名稱（別名如「傻眼」「憤怒」請先轉成「無言」「生氣」），不可使用停用標籤；作品與主要角色可寫成自由標籤，但不要把僅出現一次的台詞字眼重複造標籤。清單沒有合適的分類詞才新增，並在最後列出新增的標籤（排除作品／角色名）與理由。

【現有標籤清單：貼上 list-tags 指令的輸出】
- usage：舉一個自然的聊天使用時機；情緒難辨時可填 null。若有僅適合單張圖的口語替代搜尋法，可補充於此或交付摘要供入庫後填進管理頁「也可以這樣搜」。

若提供舊清單，先比對再補新圖。同圖轉貼或單純縮放只留來源最清楚的一筆；同底圖換台詞且改變笑點，可保留兩版並在描述註明差異。若無法比較既有圖庫，回報去重範圍。最後列出新增張數、合併的重複數與待查項目。
```

## 合併多份成果

```text
請合併附件中的既有清單與各 Agent 的新清單。保留既有紀錄，逐張比對來源、畫面與台詞，把同一張圖的轉貼合併，補上遺漏的來源或搜尋詞。確認每筆都保留本批指定的搜尋詞，使用 speaker（非 character），無字圖的 caption 是 null，標籤數量 1～6 個且已將別名轉成正式名稱。交付完整合併版 JSONL，另列新增、補充、重複與待查數量。無法判定是否同圖的項目保留供人工比對。
```

## 與本專案銜接

有專案存取權的 Agent 完成搜尋或整理本機資料夾後，先執行技能檢核腳本確認欄位、標籤與重複項，再將候選 JSONL 和可直接開啟的 HTML 一起交付。在專案根目錄執行以下指令，替換成當次批次的檔案路徑；若候選使用 `local_path`，需加上 `--local-root 本機圖片根目錄`。若已有核准清單，加上 `--approved 核准清單.jsonl` 可設定首次開頁的勾選。

```powershell
.venv\Scripts\python.exe .agents/skills/review-meme-candidates/scripts/validate_candidates.py data/web-meme-import/候選清單.jsonl --local-root 本機圖片根目錄
.venv\Scripts\python.exe scripts/build_meme_review.py data/web-meme-import/候選清單.jsonl --local-root 本機圖片根目錄
```

網路蒐集的候選與核准 JSONL 放在 `data/web-meme-import/`。視覺化審核固定開啟 `data/web-meme-import/review.html`；切換批次前先匯出要保留的核准清單，再用上方指令更新同一頁。多批清單可用主題命名。

每張圖的「圖片出處」填實際找到圖片的貼文或網頁永久連結，保留能辨認該圖的查詢參數。「圖片直連」填媒體網址，本機圖或缺少直連時可留空。轉貼頁只能證明找到圖片的位置，原作者需另有依據。兩個網址都隨核准資料匯出。

審核頁預設展開畫面描述與使用時機。上方切到「圖庫預覽」可檢查收錄欄位；兩個模式都能用「編輯收錄欄位」修正內容。儲存會更新本機預覽，匯出時採用修改後的值。管理端的手動新增與編輯頁也提供選填的圖片出處，保存在 `meme_entries.source_page`。

### 將核准清單匯入私人圖庫

搜尋 Agent 提供可讀取的圖片直連（或本機批次提供 `local_path` 與 `--local-root`）與來源頁即可。核准後，由匯入工具驗證格式與大小（JPG／PNG／WebP／GIF，上限 32 MiB）並歸檔寫入；一般網路批次可省去預先下載整批候選的步驟，但若來源為會過期的 CDN 網址（如 Threads／IG 簽名網址）建議盡快匯入。

```powershell
.venv\Scripts\python.exe -m src.anime_memes import-web-memes data/web-meme-import/approved_memes.jsonl --local-root 本機圖片根目錄
.venv\Scripts\python.exe -m src.anime_memes import-web-memes data/web-meme-import/approved_memes.jsonl --local-root 本機圖片根目錄 --apply
```

第一行預檢，第二行實際下載或讀取本機圖歸檔並逐筆寫入（非本機批次可省略 `--local-root`）。結果按清單行號回報。成功項目保存作品、角色、標題、台詞、標籤及網址；描述與使用時機留在審核檔。若遇舊版無法讀取的本機附件，匯入器會回報 `legacy_attachment` 錯誤並暫停寫入該批本機圖（標記 `skipped`）以維持跨批去重保證。修正失敗項目後可重跑，同一圖片直連或相同 SHA-256 的本機圖已由此工具匯入時會跳過為 `duplicate`。不同網址指向同一張圖，以及 Discord 匯入的既有館藏，仍需在產出候選與審核階段檢查去重。

圖片入庫後，可在管理頁明細用人工標籤或「也可以這樣搜」補上需要的搜尋詞，儲存後搜尋確認。若交給 Agent 標註既有圖片，請照[圖片標籤操作手冊](agent-image-tagging-guide.md)匯出工作並選用 taxonomy 允許的 ID。清單中的自由搜尋詞可供人工補標參考。
