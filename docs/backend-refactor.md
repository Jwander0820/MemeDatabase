# 雙版本後端與發布操作

更新於 2026-11-24。本文涵蓋私人發布管理、公開搜尋頁、API 與本機 Docker 操作。

## 架構與模組

```text
本機管理 UI（靜態檔） → 管理 Flask／Waitress
                             ├─ management：寫入、上傳、刪除
                             ├─ queries：完整管理庫搜尋
                             ├─ publishing：檢查與公開包匯出
                             └─ SQLite＋本地圖片
私人 MCP（可選、唯讀） ───────→ queries

公開 Nginx ─ /api/v1/ → 公開 Flask／Waitress → public_data → 公開 SQLite
           └ /media/  → 內容雜湊 WebP 縮圖

兩種資料查詢共用 search 的正規化與計分；releases 處理版本驗證、切換與清理。
```

`web.py` 保留原管理入口及查詢函式匯入相容性，管理 HTTP 路由轉呼叫管理操作；不再包含搜尋 SQL、上傳交易或刪除流程。管理 HTML 不使用模板注入。公開服務由 runtime 的 public 模式啟動，沒有 import 管理／圖片／OCR 模組，也沒有管理或 MCP 路由。

本機保留 `.venv` 完整環境。部署依賴分為：

| 清單 | 用途 |
|---|---|
| requirements.txt | 公開服務：Flask、Werkzeug、Waitress、OpenCC |
| requirements-admin.txt | 基礎＋Pillow，本機／私人管理與發布 |
| requirements-ocr.txt | 管理＋既有 PaddleOCR GPU，引擎仍需相容本機環境 |
| requirements-mcp.txt | 基礎＋官方 mcp 2.3.0，私人唯讀選用 |

所有預設 Docker target 不安裝 OCR。公開 Python target 只複製執行公開 API 必要模組；公開 Nginx target 不含管理前端。私人 MCP target 不含 Pillow 或完整管理庫寫入程式。版本固定的是直接依賴，尚非完整 transitive lockfile。

## 本機 migration 與管理接口

新增 `meme_entries.publish_selected`（預設 0）、`public_id`（nullable、唯一）與 `attachments.public_original_url`。公開 ID 在第一次選擇發布時產生，取消再選不變；不把管理 key 或附件 ID 放進公開庫。原本 ready 不代表同意公開，亦不自動採用 Discord／r2_url 作原圖網址。

主庫 migration 已在副本重跑並比對原欄位，正式執行前備份為 `data/backups/anime_memes_before_sync_20261124_121103_842384.sqlite3`。原 1,076 筆內容不變，公開選取數 0，外鍵與完整性檢查通過。自行部署舊資料前先備份，再執行既有 init-db；公開模式不 migration。

新增管理端接口：

| 接口 | 行為 |
|---|---|
| GET /api/entries/{key}/publication | 查公開選擇、公開 ID、原圖網址 |
| PATCH 同路徑 | 僅接受 publish_selected 布林值與 public_original_url 字串／null；省略保留、空值清除網址 |
| POST /api/publication/check | 一致快照檢查，逐項 errors／warnings |
| POST /api/publication/export | 無參數或空物件；匯出至服務設定目錄，禁止指定任意路徑 |

私人 `POST /api/publication/activate` 只接受 `{"version":"版本號"}`，驗證發布包後切換並回傳 version、count。

匯出成功回傳 `exported: true`、`version`、`report`；資料錯誤回傳 422 與 `exported: false`，整批阻擋；不自動啟用。缺縮圖、非 ready、非法原圖網址、公開文字疑似本地路徑等列為錯誤；無原圖網址是允許的縮圖限定發布。空選取集合可以匯出，報告警示將清空公開館藏。

外部原圖只接受無帳密 HTTPS，阻擋 localhost、明確非公開 IP 與非法格式；不做 DNS／下載檢查，也不保證網址可用。發布檢查無法替代人工內容審查。匯出加檔案鎖限制同時一個作業；若程序異常退出留下 .export.lock，需確認沒有匯出程序後再移除。

## CLI：選擇、檢查、匯出

在專案根目錄執行，key 使用本機管理 API 回傳的值：

```powershell
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication select meme_EXAMPLE --selected yes
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication select meme_EXAMPLE --original-url https://images.example.com/image.webp
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication check
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication export --root data/releases
```

取消公開用 `--selected no`，清空原圖網址用 `--original-url ''`。取消選擇或刪除本機項目後，需再次匯出並啟用新版才會從公開搜尋移除；歷史版本與長快取媒體不會因此立即失效，緊急撤回須另停用入口並清理舊版本／CDN。check／export 不合格 exit code 2；其他執行錯誤非零。API 的輸出根目錄由 create_app 的 release_root 或私人 runtime 的 ANIME_MEMES_RELEASE_ROOT 設定；CLI 明確使用 --root。

匯出先用 SQLite backup API 取得一致管理快照，再將選定欄位輸入全新公開庫。縮圖重新解碼／編碼為最長邊 420 px、WebP quality 78，不放大，重建像素去掉 EXIF／ICC。原始縮圖與原圖不被覆蓋。原圖網址可以省略，公開端不代理外部圖片，也不執行縮圖。

## 發布包與熱更新

```text
releases/
├─ current.json                   目前版本指標
├─ versions/<version>/
│  ├─ public.sqlite3              僅公開白名單表與欄位
│  └─ manifest.json               版本、筆數、DB／媒體雜湊
├─ media/<sha256>.webp            縮圖，供 Nginx 掛載
└─ reports/<version>.json         僅管理端使用的檢查紀錄
```

同步至目標發布目錄時，先完整放好 media 與 versions，最後在**目標主機**驗證並啟用，勿把上傳中的檔案直接指定成目前版本，也不要同步覆寫 current.json。

```powershell
.venv\Scripts\python.exe -m src.anime_memes.runtime validate --root data/releases VERSION
.venv\Scripts\python.exe -m src.anime_memes.runtime activate --root data/releases VERSION
.venv\Scripts\python.exe -m src.anime_memes.runtime rollback --root data/releases OLD_VERSION
```

validate 核對 schema／搜尋規則版本、完整性、白名單、ID／網址、筆數及雜湊。activate／rollback 都只在成功驗證後原子切換 current.json。每個請求固定一個版本並開啟唯讀交易，完成後關閉；無需全庫 dict 或重啟服務。Windows 短暫檔案分享衝突有最多約一秒的 replace 重試，失敗保留原指標。

管理資料／報告與公開資料分開；Nginx **只掛載 media**，不可供應 versions、reports 或 DB。對外 API 每個回應帶版本號，分頁可用 `version=VERSION` 固定版本；已移除版本回傳 410 version_expired。沒有啟用版本時 readiness 503、liveness 200。

版本清理先停止公開服務，再執行；明確旗標是操作者對已停服務的宣告，CLI 不會替你停止程序：

```powershell
docker compose --profile public stop
.venv\Scripts\python.exe -m src.anime_memes.runtime prune --root data/releases --service-stopped
docker compose --profile public up -d
```

保留最新三個版本及目前啟用版本，其餘版本與不再引用的媒體才刪除。檢查報告另行保留。不要在服務啟動時自動清理，也不建立午夜排程。

## 公開搜尋契約

| 接口 | 用途 |
|---|---|
| GET /api/v1/entries | q、tag、sort、page、page_size、version |
| GET /api/v1/entries/{id} | 單筆，僅接受 version |
| GET /api/v1/meta | 正式標籤與數量，僅接受 version |
| GET /health/live | 程序可回應 |
| GET /health/ready | 已啟用且 schema 相容 |

列表預設每頁 24、最多 60，page 1–10000；sort 為 relevance、updated、title。q 最長 256 字／16 詞。有查詢預設相關度，否則最近更新；保留繁簡、標點、多詞、標籤／別名搜尋規則。不接受 status、favorite、missing_image 或其他管理參數。

列表回傳 `{version, items, total, page, page_size}`；單筆 `{version, item}`。item 欄位為 id、display_title、resolved_title、speaker、anime_title、quote_text、tags、search_aliases、thumbnail_url、original_url、updated_at；ocr_text 只參與搜尋，不直接回傳。API 無長時間快取，圖片雜湊 URL 使用一年 immutable 快取。

錯誤格式為 `{error: {code, message}}`，涵蓋 400 invalid_request、404 not_found、405 method_not_allowed、410 version_expired、503 not_ready。Nginx 限流回傳 429；其預設 HTML 錯誤頁不遵循 API JSON 格式，前端須先判斷 HTTP status。

公開版只提供網站需要的 API，不提供 Agent 專用路徑、token 平台或 MCP。匿名 HTTP 搜尋仍可能被自動化呼叫，沒有宣稱能辨別或阻止所有機器人。

## Docker／Nginx 本機啟動

首次先產生並啟用發布包，公開 API 才會 ready；私人版可先啟動管理。Linux bind mount 的資料權限須允許容器 UID 10001：私人資料需可寫，公開 DB／媒體需可讀。既有 DB 的 Windows 媒體路徑透過設定的掛載根目錄映射 basename，不改寫原紀錄；原始資料、模型與秘密不打進映像。

```powershell
# 同一個 PowerShell 工作階段使用隔離測試資料
$env:PRIVATE_DATA_ROOT = './data/frontend-qa-1124/private'
$env:RELEASE_ROOT = './data/frontend-qa-1124/releases'
docker compose -p animememes-frontend-qa --profile private up -d --build --wait
# 在私人頁完成首次啟用後執行
docker compose -p animememes-frontend-qa --profile public up -d --build --wait
```

1. 開啟[私人管理頁](http://localhost:8765)，手動新增測試圖，填入作品「刃牙」、角色「烈海王」、台詞「復！活！」及標籤「復活」。在詳情勾選「納入公開館藏」並儲存。原圖網址可留空以測試僅發布縮圖。
2. 在「發布管理」按檢查。逐筆問題可點管理 key 開啟修正。匯出後確認版本與筆數，再按「啟用此版」。選取零筆時，啟用後公開搜尋會變空。
3. 啟動公開 profile，開啟[公開搜尋頁](http://localhost:8875)。搜尋「復活」、套用標籤、開啟詳情、下載縮圖，將頁面連結貼到新分頁驗證。原圖為選填的外站資源，剪貼簿或下載受限時可開啟原圖或下載本站縮圖。
4. 在私人頁取消選取後再檢查、匯出，此時重新搜尋仍可找到該圖。啟用新版，再按公開頁「搜尋」，確認結果更新。後續換版可直接在頁面操作。
5. 結束測試可執行 `docker compose -p animememes-frontend-qa --profile public --profile private down`。測試館藏保留於上述主機目錄。

Nginx 代理 API、健康檢查及 media，停用目錄列表；預設每 IP 2 次／秒、burst 10，SEARCH_RATE／SEARCH_BURST 可覆寫。未信任任意 X-Forwarded-For；部署到既有代理或 Tunnel 時應另外設定明確可信來源，否則多位使用者可能共用入口 IP 的限流額度。

| 環境變數 | 預設／用途 |
|---|---|
| PRIVATE_DATA_ROOT | ./data，私人完整資料掛載 |
| RELEASE_ROOT | ./data/releases，發布資料掛載 |
| PRIVATE_PORT／PUBLIC_PORT | 8765／8875，主機 loopback port |
| SEARCH_RATE／SEARCH_BURST | 2r/s／10 |
| PRIVATE_URL | http://127.0.0.1:8765，MCP 回傳給呼叫端的媒體基址 |
| ANIME_MEMES_MCP_TOKEN | 無預設，至少 32 ASCII 字元、無空白 |

Compose 預設公開後端上限 192 MB、Nginx 64 MB、私人後端 384 MB、MCP 192 MB；這些是可調整的限制，並非測得容量保證。各 Python 程序單程序 4 Waitress threads，Nginx 1 worker；Docker 日誌 5 MB × 3 份，含健康檢查與 unless-stopped。不開管理面板；沒有直接對外 TLS 配置，VPS 的 HTTPS／Tunnel 留待部署階段。

資料更新不重建映像。程式更新使用 `up -d --build`；回復映像與相容資料版本應一起處理。一般 stop／down 不刪 bind mount 資料。

## 私人選用 MCP

```powershell
# token 請自行安全產生並透過環境注入，不提交實際值。
$env:ANIME_MEMES_MCP_TOKEN = '<至少32字元的隨機ASCII值>'
docker compose --profile private --profile mcp up -d --build
```

預設 `http://127.0.0.1:8766/mcp`，使用 `Authorization: Bearer TOKEN`，無狀態 Streamable HTTP。僅 search_memes(query, limit=5，最多20) 與 get_meme(id)，查 ready 資料，共用本機搜尋。metadata 有回傳數量與文字長度上限，不回傳 notes、OCR、來源訊息或檔案路徑。未提供／錯誤 token 回 401，錯誤 Host／Origin 回 403，不存在寫入工具。

非 Docker 可 `python -m src.anime_memes.private_mcp`；預設 loopback，依環境設定 ANIME_MEMES_DB、ANIME_MEMES_PRIVATE_URL。自行換 port／網域時也必須設定精確的 ANIME_MEMES_MCP_HOSTS／ANIME_MEMES_MCP_ORIGINS 清單，勿用萬用允許值。私人管理端沒有新增遠端登入系統，因此 loopback 不可直接改成公開曝光。

## 驗證紀錄與限制

測試涵蓋既有功能、公開欄位隔離、整批阻擋、縮圖／路徑、版本固定與並行切換、損壞回復、唯讀連線、MCP 協定與權限。容器已實際建置並驗證 Nginx 設定、管理靜態頁與圖片、公開 API、縮圖快取與限流、公開映像無 Pillow／OCR／MCP、掛載 DB 寫入失敗。

真實 Docker 整合測試 `tests/test_containers.py` 已通過，耗時約 95 秒：臨時管理庫的 API 修改及匯出、容器外啟用新版、公開 API 不重啟即讀新版、指定舊版、rollback、強制重建公開容器後版本保留，以及私人 MCP 握手／搜尋皆成功。測試自行建立臨時資料並在 finally 關閉隔離 Compose 專案，不碰正式館藏。重跑方式：

```powershell
$env:RUN_CONTAINER_TESTS = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_containers.py -v
Remove-Item Env:RUN_CONTAINER_TESTS
```

需可存取 Docker daemon、套件來源與 loopback，測試使用主機 port 18865、18875、8766，請先確定未被其他程序占用。一般回歸不啟用此環境變數，GPU smoke test 同樣需要另行 opt-in。容器測試時公開 API 約 27 MiB、Nginx 約 4 MiB，僅小型 fixture 的瞬間值，非正式負載或容量上限。

使用實際館藏副本中的 981 筆合格候選，暖查詢中位數約 11–14 ms；複製欄位擴充到 9,810 筆約 122–133 ms。Windows 測量程序包含 Pillow／匯出模組，RSS 約 42–45 MB，不代表公開容器 RAM。標籤查詢計畫使用 idx_public_tag 覆蓋索引，任意文字仍掃描，不宣稱全文索引加速或 VPS 容量。

VPS 部署、TLS、遠端管理帳號、PostgreSQL 與 OCR Docker 留待後續規劃。
