# 2026-09-24 維護與驗證紀錄

## Discord 同步

- 2026-09-23 23:50 執行一次 `src/commands/sync_discord.cmd`。
- 匯出程式在讀取頻道前回報 `Authentication token is invalid`，exit code 1。沒有取得或匯入新訊息。
- 執行前已以 SQLite backup API 備份至 `data/backups/anime_memes_before_maintenance_20260923_235030.sqlite3`。
- 遮蔽 token 的執行紀錄：`data/sync_20260923_235030.log`。
- 首次失敗後依使用者指示先完成程式修正，當時未推進成功游標。
- 2026-09-24 01:44 更新 token 後重試，01:51 完成：172 則新訊息、92 筆新 entries、92 個附件下載及 92 張縮圖；原有 984 個附件使用快取。entries 總數為 1,076。
- export run 5 為 `success`；成功游標已更新，最新訊息為 `2026-09-23T08:00:12.926+08:00`。成功時間 `2026-09-23 17:51:07` 為資料庫 UTC，對應台灣時間 09-24 01:51:07。
- 新增資料有 5 筆 `needs_review`，其中 4 筆 `unpaired`；既有 22 筆候選標籤仍保留待審。
- 重試前自動備份：`data/backups/anime_memes_before_sync_20260924_014445_999861.sqlite3`。
- 本次外層紀錄程式因 cp950 無法列印頻道 emoji 中斷，但背景同步持續完成；沒有重複啟動同步。原始程序 exit code 未取得，完成依據為成功紀錄、游標及本機檔案驗證。
- 唯讀驗證結果存於 `data/sync_20260924_014445_verification.json`；主庫 `quick_check=ok`、foreign key 無錯誤，附件及一般圖片縮圖皆無缺漏。
- 2,140 個一般圖片原檔／縮圖解碼皆成功。部分附件 metadata 大小與本機檔案不同；抽樣以相同下載請求確認 HTTP Content-Length 與本機大小一致，沒有因此覆寫檔案。

## 修正內容

| 問題 | 修正 |
|---|---|
| 每次 Web 請求都初始化、重建索引及改寫標籤 | 移至啟動時初始化；重複初始化不改 schema 或既有標籤 |
| taxonomy 改名造成唯一鍵錯誤，alias 未映射 | 依穩定 ID 遷移，保留正式／候選關聯；支援名稱交換、alias 正規化與同來源去重 |
| 已遷移的未知舊標籤仍啟用 | 修正 legacy 狀態，保留有明確描述的人工自訂標籤 |
| 損壞 PNG 被匯出給 Agent | 實際驗證及解碼圖片，另計 `skipped_invalid` |
| 未完成所有工作就將批次標為完成 | 必須所有工作已處理且無 pending 候選 |
| 停用標籤仍能接受；競爭決策可能新增錯誤正式標籤 | 接受前檢查可用性；檢查條件更新結果，衝突回傳 409 |
| 舊 LLM 匯入接受無限信心值，錯誤行留下部分候選 | 先完整驗證每行，符合限制才寫入；錯誤時 CLI 回傳非零 |
| 同步失敗把 token 放入例外內容 | exporter 不再重印完整 argv，CLI 再遮蔽 token |
| 備份在 migration 之後，直接複製可能漏 WAL | 初始化前使用 SQLite backup API |
| 附件／縮圖失敗仍更新成功游標 | 記錄 partial，保留重試範圍、exit code 2，下次重試失敗附件 |
| 中斷下載留下半檔被誤認為快取；force 無法覆蓋快取 | 同目錄暫存、檢查長度、完成後取代；force 真正重新下載，失敗保留舊檔 |
| Discord 下載流程處理手動上傳項目 | 排除 Manual 訊息附件 |
| 審核重載 drawer 清掉未存草稿；人工欄位混入其他來源 | 只更新候選卡片與正式標籤，人工欄位只使用 manual_tags，錯誤後按鈕可重試 |

## 驗證

- 新增 `tests/test_maintenance.py`，前次問題及額外狀態／下載問題皆有回歸案例。
- 主庫副本初始化前後：984 entries、106 tag links、22 suggestions、1 tagging run；筆數一致。
- 副本 `foreign_key_check` 無錯誤，`quick_check=ok`；第二次初始化的 schema version 與 total_changes 均不變。
- 隔離資料庫的實際瀏覽器驗證：接受後保留台詞與人工標籤草稿；儲存後來源正確；衝突錯誤可恢復，拒絕不新增正式標籤。
- `python -m unittest discover -s tests -v`：35 個測試，34 通過、1 個 GPU smoke test 依環境跳過。
- `python -m compileall -q src tests`、`node --check src/anime_memes/web_static/app.js`、`git diff --check` 均通過。

## 使用限制

- 已以有效 token 完成真實同步；新增資料中的人工檢查項目尚未處理。
- Web 服務須重啟才能使用新程式及重新同步 taxonomy。
- 自動測試使用暫存資料庫；正式資料庫已於本次成功同步時執行新版 migration。
- 本次沒有提交或推送 Git 變更。
