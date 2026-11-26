# 搜尋後端實作紀錄

最初實作日期：2026-11-23；現況說明更新：2026-11-26。公開 API、R2 720P 大圖、發布／回復、容器、私人 MCP，以及管理頁名稱／替代詞／完整標籤編輯均已有實作。最新狀態與命令請讀[雙版本後端與發布操作](backend-refactor.md)及[本機匯入與編輯規格](local-import-editor-spec.md)。以下主庫遷移與效能數值保留當時日期，不代表目前館藏筆數或正式主機狀態。

## 資料庫

SQLite migration 新增 nullable display_title 及 entry_search_aliases，重跑不清空資料。替代詞以 entry_key、alias_normalized 唯一，刪除 entry 時連帶刪除。新增 entry_tags(tag_name, entry_key) 索引供標籤篩選使用；文字搜尋仍需掃描，未宣稱全文索引加速。

主庫已先用 backup API 複製至暫存副本，重跑 migration 兩次並比較原有 meme_entries 所有欄位。正式遷移前備份：`data/backups/anime_memes_before_sync_20261123_235318_301722.sqlite3`。正式遷移後 1,076 筆舊資料原欄位一致，外鍵檢查通過。沒有加入範例梗圖。

## 本機 API

- GET /api/entries、GET /api/entries/<key> 新增 display_title、resolved_title、search_aliases（陣列）。resolved_title 是顯示用後備名稱，不寫回原欄位。
- PATCH /api/entries/<key> 可傳 display_title（字串或 null）、search_aliases（文字陣列）。省略表示不修改，null 名稱／空字串清空名稱，空陣列清空替代詞。兩者可與既有欄位在同一交易更新；無效替代詞不留下部分更新。
- POST /api/entries/manual 支援 multipart display_title 與 search_aliases；後者為 JSON 陣列字串，省略為空陣列。
- 替代詞最多 50 筆，每筆最多 200 字；正規化後去重，原文保留。查詢最多 256 字、16 個詞，無效查詢回傳 400。
- 這些是既有本機管理 API，仍不可直接公開。

```json
{
  "display_title": "烈海王復活",
  "search_aliases": ["我又回來了", "滿血復活"]
}
```

## 搜尋

共用 search.py 實作 NFKC、casefold、既有 OpenCC 繁簡轉換及緊縮比對。% 與 _ 保留為字面符號，沒有 SQL 萬用字元語意。整句緊縮命中或各詞跨欄位 AND 命中均可；不搜尋 notes 或待審候選。

以 SQLite 註冊的純計分函式直接讀取最新原始欄位、正式標籤與替代詞，不保存衍生全文投影，因此匯入、OCR 與審核寫入後不用另跑重建。正規化快取有容量限制；替代詞保存 normalization_version=1，未來變更規則仍需版本化遷移處理唯一鍵碰撞。

未指定 sort 且有查詢時使用相關度；sort=relevance 可明確啟用。管理頁目前預設「自動」排序，無查詢時按更新時間、有查詢時按相關度，並已提供名稱與替代詞欄位。公開搜尋頁與本機 `/browse` 已共用搜尋及瀏覽介面。

2026-11-23 本機副本單次「復活」搜尋約 95 ms，屬歷史測量，非壓測或容量保證。沒有引入 OCR／模型至搜尋請求，也未加入 PostgreSQL 或語意搜尋。

## 驗證範圍

搜尋驗收涵蓋標點／繁簡、多詞、替代詞、精確標籤交集、相關度、重複不加分、原文保存、無效更新回復、刪除連帶清除、冪等 migration 及內部備註排除。UI 整合、公開發布包安裝／換版／rollback 已有測試；私人館藏完整還原、VPS 壓測及依實測決定的索引優化仍待完成。
