# 個人迷因資料庫：同類方案與部署研究

查證日期：2026-11-23。此文件是外部方案研究，沒有安裝或壓測以下工具，也沒有部署本專案。價格為美元，實際 VPS 餘裕仍需量測。

## 已有人做過的方向

| 方案 | 官方已提供的能力 | 對本專案的參考價值與限制 |
| --- | --- | --- |
| [Meme Search](https://github.com/meme-search/meme-search) | 自架迷因庫、標籤、關鍵字／向量搜尋、模型產生描述、唯讀 bearer token Search API v1、CLI | 最貼近需求，可參考「共用搜尋核心＋獨立整合 API」。使用 Rails、Python、PostgreSQL/pgvector；官方僅支援 loopback，UI 沒有使用者認證，不能直接當公開網站部署。 |
| [Meme Tracker](https://github.com/lucasaid/meme-tracker) | 圖片／影片、OCR／語音／AI 描述與標籤、精確與語意搜尋、私人使用者資料庫；PostgreSQL/pgvector、FastAPI、分析 worker、React | 可參考搜尋與審核流程。但涉及更多模型及服務；官方建議私人網路存取，現成 Compose 並非直接公開的完整方案。 |
| [Kensaku](https://github.com/yjid/kensaku) | 本地檔案搜尋、圖片 OCR、CJK 支援、語意搜尋、CLI／MCP，HTTP 介面限 loopback 並要求 bearer token | 不是迷因管理網站，但可參考「同一個本地索引供 UI 和 Agent 共用」，MCP 回應大小限制與預設唯讀設計。 |

以上功能來自各專案當次 README，未驗證繁體中文迷因實際命中率。其他專案存在，能證明方向有實作先例，不能保證直接替換本專案更省事。

## R2 成本與存取

R2 Standard 每月免費額度為 10 GB-month 儲存、100 萬次 Class A、1,000 萬次 Class B；超額分別為 $0.015／GB-month、$4.50／百萬次、$0.36／百萬次。對外 egress 免費；串接其他計量服務仍可能另收費。免費額度不適用 Infrequent Access，帳單會依計費單位進位。[官方價格](https://developers.cloudflare.com/r2/pricing/)

假設整月固定儲存、帳號免費額度未被其他專案占用，僅算 Standard 儲存：20 GB 約 $0.15／月、50 GB 約 $0.60／月、100 GB 約 $1.35／月。這是依官方單價計算的估算，不包含請求、網域、VPS、其他服務與稅費。既有 R2 用量會共享帳號額度。

正式公開媒體應使用自訂網域；`r2.dev` 只供開發用途且有限流。自訂網域可搭配快取、WAF、Access。若使用 Access 保護，須關閉另一條公開的 `r2.dev` 入口。[官方公開 bucket 說明](https://developers.cloudflare.com/r2/buckets/public-buckets/)

**架構推論：** API 只回傳文字、標籤、縮圖及原圖 URL，讓瀏覽器／Agent 直接從 R2 取媒體，便可避免這部分圖片流量經過 VPS。若後端把 R2 圖片下載後再代理給使用者，VPS 流量仍會消耗。Tunnel 也不會自動消除主機送出資料的成本。

## PostgreSQL 與中文短詞

`pg_trgm` 可用 GIN／GiST 加速 `LIKE`／`ILIKE` 等查詢；官方提醒，無可抽取 trigram 的 pattern 會退化為完整索引掃描。[PostgreSQL 官方文件](https://www.postgresql.org/docs/17/pgtrgm.html)

**對本案的推論：** `復活` 是兩字詞，不能假定加上 trigram 索引就讓 `%復活%` 很快。先建立可量測的基準查詢，搭配正式標籤、角色／作品與別名，必要時保留小資料量 substring 搜尋；資料增加後再評估中文斷詞或 bigram 搜尋。單換 PostgreSQL 不會讓系統自動理解「復活 → 烈海王」。

## 建議的資源分工（設計建議，非實測）

- 本地：原圖保存、匯入、OCR、視覺模型、人工審核；重工作批次執行。
- VPS：Web／唯讀搜尋 API、管理認證、資料庫；不讓每次搜尋觸發 OCR 或大模型推論。
- R2：縮圖與原圖直接分發；列表只載入縮圖，點開再取原圖。
- Agent：先呼叫小型 JSON API，限制結果數並回傳 URL；有需要再加薄的 MCP 包裝。MCP 協定本身不等於必須在 VPS 上常駐 LLM。
- 本地副本：先選定單一寫入主來源，再做單向發布及可還原備份，避免一開始就維護雙向同步衝突。

是否值得換用其他專案，應以同一組繁體中文測試題比較命中率、分享流程及實際常駐記憶體；不建議只因別人已有語意搜尋就放棄既有 Discord 匯入、人工修正及審核資料。
