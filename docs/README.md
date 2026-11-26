# 文件索引

新工作先閱讀整體規格，再依需求查 DB 規格或操作手冊。規格中的目標功能不代表已實作；目前可用操作以專案 README 與程式為準。

| 文件 | 用途 | 狀態 |
|---|---|---|
| [雙版本後端與發布操作](backend-refactor.md) | 管理／公開服務、發布 API／CLI、版本切換、Docker／Nginx、依賴鎖檔、私人 MCP | 現行架構與操作入口 |
| [VPS 部署手冊](vps-deployment.md) | Cloudflare Tunnel、首次部署、推送版本、回復與日常維運 | 部署操作；主機負載與還原驗收另行記錄 |
| [本機圖片匯入與管理頁編輯規格](local-import-editor-spec.md) | 圖片去重、草稿保護、整筆儲存、名稱與替代詞搜尋 | 行為與驗收條件 |
| [手動新增與本機匯入修正規格](manual-upload-import-fixes.md) | 中文檔名、舊回應隔離、建檔與畫面更新分開回報、舊附件核對 | 已實作，自動測試使用隔離館藏 |
| [搜尋後端實作紀錄](backend-search-implementation.md) | 已實作 schema、API、搜尋行為與遷移驗證 | 早期實作紀錄，管理頁現況見本機匯入與編輯規格 |
| [整體規格](personal-meme-platform-spec.md) | 產品目標、雙版本、前後端分離、Nginx／Docker、發布與熱更新；第 15 節列實作差距 | 現行目標，部分完成 |
| [品牌圖示與前端色彩規範](ui-brand-and-colors.md) | 03 版 `M↻E` 四格漫圖示、四階濃淡紫主色色票、WCAG 對比與深淺色 Token 指引 | 公開／管理頁已共用品牌與深淺色 Token |
| [DB 與搜尋規格](database-search-spec.md) | 欄位、替代詞、搜尋契約、索引與 migration | 本地搜尋與管理詳情已整合 |
| [Agent 圖片標籤操作手冊](agent-image-tagging-guide.md) | 現有 JSONL 工作匯出、候選匯入與審核 | 現行操作手冊 |
| [委託 Agent 蒐集新梗圖](agent-meme-collection.md) | 可複製的搜尋提示詞、無字圖標記與跨批去重 | 現行操作手冊 |
| [歷史文件](archive/README.md) | 已完成階段設計、工作紀錄、維護及背景研究 | 歷史參考 |

## 維護原則

- 現行規格及仍適用的操作手冊留在 docs 主目錄。
- 階段結束後的紀錄、被整合的背景評估放入 archive，保留原內容與時間背景。
- 歷史資料不作為目前進度或現行價格的證明；需要時查程式、最新測試與第一手來源。
- 文件移動須同步修正連結。變更需求直接更新現行規格，避免建立多份內容重複的「最新版」。
