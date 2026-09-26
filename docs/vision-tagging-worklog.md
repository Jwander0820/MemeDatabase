# 視覺 LLM／Agent 自動標籤工作紀錄

- 對應規格：[`vision-tagging-stage-spec.md`](vision-tagging-stage-spec.md)
- 建立日期：2026-07-13
- 階段狀態：實作與端到端驗收完成；22 個真實候選待人工審核
- 目前負責人：未指定

## 本階段目標

完成模型無關的圖片標籤基礎管線，讓 Codex、Antigravity 等可讀圖 Agent 能依 taxonomy 產生候選標籤，經受控匯入與人工審核後寫入正式標籤；本階段不串接本地 LLM provider。

## 決策紀錄

| 日期 | 決策 | 原因 |
|---|---|---|
| 2026-07-13 | 模型結果先進入候選，人工審核後才成為正式標籤 | 避免第一版大量誤標污染正式資料 |
| 2026-07-13 | 核心採 provider-neutral JSONL 協定 | 同時兼容本地 LLM、Codex、Antigravity 與後續工具 |
| 2026-07-13 | 本階段完成共通協定與 Agent 手冊，本地 LLM adapter 延後 | 先穩定資料契約、審核與安全邊界 |
| 2026-07-13 | taxonomy v2 先做完整結構與核心詞表 | 兼顧可實測性，避免一次設計過大的詞庫 |
| 2026-07-13 | Agent 不直接寫 SQLite | 所有輸入需通過專案驗證與可追溯寫入流程 |
| 2026-07-13 | 正式標籤 schema 採加法式相容 migration | 保留既有 `name/tag_name/source` 一個版本，降低 984 筆主資料遷移風險 |

## 進度總覽

狀態標記：`[ ]` 未開始、`[-]` 進行中、`[x]` 完成、`[!]` 阻塞。

- [x] 盤點既有 LLM 標籤入口、schema、Web API 與本地資料現況
- [x] 定義本階段目標、邊界、接口、資料模型及驗收條件
- [x] 建立完整規格文件與工作紀錄
- [x] 確認設定檔、範例與專案文件為正確 UTF-8，加入中文字 round-trip 驗證
- [x] 建立 taxonomy v2 loader、validator 與核心詞表
- [x] 完成正式標籤 schema migration 及舊資料映射
- [x] 建立 tagging runs 與 tag suggestions 資料模型
- [x] 實作 `export-tagging-jobs`
- [x] 實作 `import-tagging-results` 與舊入口相容行為
- [x] 實作 Web UI 候選標籤審核流程
- [x] 撰寫 Codex／Antigravity 共用 Agent 操作手冊及範例
- [x] 補齊單元、整合與 regression tests
- [x] 使用 10 張圖片完成端到端驗收
- [x] 更新 README、AGENT.md 並完成階段收尾

## 執行批次

每次工作開始或完成時，在此表新增一列；`輸出／證據` 應填入 commit、測試命令、檔案或驗收結果。

| 日期 | 工作項目 | 狀態 | 輸出／證據 | 備註 |
|---|---|---|---|---|
| 2026-07-13 | 現況盤點與架構討論 | 完成 | 984 entries、899 無標籤；現有入口只能匯入結果 | 無程式異動 |
| 2026-07-13 | 階段規格與工作紀錄 | 完成 | `docs/vision-tagging-stage-spec.md`、本文件 | 採人工審核優先 |
| 2026-07-13 | Taxonomy、資料模型與 CLI | 完成 | `config/tags.taxonomy.json`、`tagging.py`、兩個新 CLI | 舊 LLM 入口改存 pending 候選 |
| 2026-07-13 | Web 候選審核 | 完成 | 候選查詢與 decision API、detail drawer 接受／拒絕 | 接受後才寫入正式標籤 |
| 2026-07-13 | 主資料庫 migration | 完成 | 984 entries、106 entry tags 前後一致；missing tag IDs = 0 | 備份位於 `data/backups/` |
| 2026-07-13 | 10 張真實圖片驗收 | 完成 | 10 processed、22 pending、1 empty、0 errors | 第二次匯入為 22 duplicates、0 新增 |

## 驗證紀錄

| 日期 | 驗證項目 | 命令／方法 | 結果 | 備註 |
|---|---|---|---|---|
| 2026-07-13 | 文件結構檢查 | 檢查 Markdown 標題、相對連結、UTF-8 replacement character 與 Git status | 通過 | 規格與工作紀錄存在；連結有效；未發現 Unicode U+FFFD 字元 |
| 2026-07-13 | 自動測試與編譯 | `python -m unittest discover -s tests -v`、`compileall` | 通過 | 16 tests passed，1 個 GPU smoke test 依環境跳過 |
| 2026-07-13 | Migration 副本驗證 | 對主庫備份副本執行 `init_db` | 通過 | 984／106 筆數一致，所有關聯均有 tag ID |
| 2026-07-13 | JSONL 冪等驗證 | 同一 `acceptance_results.jsonl` 匯入兩次 | 通過 | 首次 22 candidates；第二次 22 duplicates |

## 問題與阻塞

目前沒有實作阻塞。剩餘操作事項如下：

- 真實驗收建立的 22 個候選仍為 pending，需由使用者在 Web UI 接受或拒絕。
- 第一輪只處理 10 筆，不會自動對其餘未標籤資料進行全量 Agent 標記。
- 相容欄位 `tags.name`、`entry_tags.tag_name/source` 至少保留一個版本，後續才評估破壞式移除。

## 工作紀錄撰寫規則

1. 開始一項工作時，把對應 checklist 改為 `[-]`，並在「執行批次」新增一列。
2. 完成時改為 `[x]`，記錄實際變更、測試命令與結果。
3. 遇到阻塞時改為 `[!]`，在「問題與阻塞」寫明原因、已嘗試方式及需要的決策。
4. 規格若有變更，先更新規格版本，再於「決策紀錄」補上日期、差異與理由。
5. 不把 token、`.env`、Discord URL 私密資料或完整資料庫內容貼入本文件。

## 下一個建議工作

啟動 Web UI 審核本次 22 個真實候選。累積足夠接受／拒絕樣本後，再評估擴充詞表或加入本地 Vision LLM adapter。

## 2026-09-24 維護

已完成同步失敗處理、標籤遷移、圖片驗證及審核介面的修正，詳見 [維護與驗證紀錄](maintenance-2026-09-24.md)。首次同步因 token 無效而失敗；更新 token 後於 09-24 01:51 成功完成，新增 172 則訊息、92 筆梗圖，附件與縮圖皆完成。主庫現為 1,076 entries、111 tag links、22 pending 候選；新增 5 筆資料待人工檢查。
