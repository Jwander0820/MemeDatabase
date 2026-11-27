# 雙版本後端與發布操作

更新於 2026-11-26。本文涵蓋私人發布管理、公開搜尋頁、API、本機 Docker 與建置鎖定；VPS 操作見[部署手冊](vps-deployment.md)。個別歷史驗證數值保留原日期與範圍，不代表當前部署狀態。

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

`web.py` 只負責組裝 Flask app（設定、初始化資料庫、請求前後處理、全域錯誤處理、`/` 與健康檢查）並註冊三個 Blueprint，同時重新匯出舊名稱以維持匯入相容性；不再包含 SQL、搜尋、上傳交易或刪除流程。路由分工如下：`web_publication.py`（發布選取、檢查、匯出、啟用、清除）、`web_admin.py`（管理 API 與媒體路由，轉呼叫 management／tagging）、`web_browse.py`（本機瀏覽 `/browse` 與 `/api/v1/*`）；共用的來源檢查、媒體回應與條目序列化在 `web_common.py`。本機瀏覽與公開 API 共用同一份驗證規則：允許參數取自 `public_data.ALLOWED_ARGS`（本機去掉 `ids`、多一個 `favorite`），未知參數與超出範圍的 `page`／`page_size` 一律回 400，錯誤格式為 `{"error": {"code", "message"}}`，由 `tests/test_web_contract.py` 守門。管理 HTML 不使用模板注入。公開服務由 runtime 的 public 模式啟動，沒有 import 管理／圖片／OCR 模組，也沒有管理或 MCP 路由。

本機保留 `.venv` 完整環境。部署依賴分為：

| 清單 | 用途 |
|---|---|
| requirements.txt | 公開服務：Flask、Werkzeug、Waitress、OpenCC |
| requirements-admin.txt | 基礎＋Pillow，本機／私人管理與發布 |
| requirements-ocr.txt | 管理＋既有 PaddleOCR GPU，引擎仍需相容本機環境 |
| requirements-mcp.txt | 基礎＋官方 mcp 2.3.0，私人唯讀選用 |

所有預設 Docker target 不安裝 OCR。公開 Python target 只複製執行公開 API 必要模組；公開 Nginx target 不含管理前端。私人 MCP target 不含 Pillow 或完整管理庫寫入程式。直接依賴由上述清單宣告，部署完整版本及雜湊由下列鎖檔固定。

## 建置鎖檔與映像更新

`requirements-locks/public-linux.txt`、`admin-linux.txt`、`mcp-linux.txt` 分別對應三種 Python Docker target，包含完整直接／間接依賴及 SHA-256。Docker 仍用 pip，搭配 `--require-hashes --only-binary=:all:`；同時讀取直接依賴與鎖檔，宣告有變更但鎖檔未同步時會因衝突或缺少雜湊而失敗，不會悄悄裝入其他版本。管理與 MCP 的鎖檔沿用公開基礎的版本限制。

更新直接依賴後，從 repo 根目錄執行：

```powershell
.\scripts\lock_dependencies.ps1
# 明確更新符合直接依賴限制的間接套件時：
.\scripts\lock_dependencies.ps1 -Upgrade
```

腳本從 Dockerfile 取得固定的 Python 基底，在同一基底的暫存 Linux 容器使用 `pip==26.3`、`pip-tools==7.7.0` 產生鎖檔；不改動本機 `.venv`，也不匯入館藏或執行同步。預設沿用鎖檔既有版本，需更新間接依賴時才加 `-Upgrade`。產出需與來源宣告一同檢查，並重建相關 target、執行測試。Linux 鎖檔不宣稱涵蓋其他作業系統、Python 版本或 OCR／CUDA；這些環境仍依各自的 requirements 清單安裝或另外產生鎖檔。

Python、Nginx 基底及預設 cloudflared 都固定「版本標籤＋多架構 digest」。單純 `build --pull` 只會拉回相同內容，不會自動取得安全修補。更新時先從原映像 registry 查證 digest，例如：

```powershell
docker buildx imagetools inspect python:3.15.1-slim-bookworm
docker buildx imagetools inspect nginxinc/nginx-unprivileged:1.30.6-alpine
docker buildx imagetools inspect cloudflare/cloudflared:2026.10.0
```

使用輸出最上層的映像索引 digest 更新 Dockerfile／`deploy/Dockerfile.nginx`／Compose，不以單一 CPU 架構的子 manifest 取代。Python 版本變更時須重產鎖檔。Dependabot 每月檢查基底映像、cloudflared 與 GitHub Actions 並開 PR（保留 digest 鎖定）；Python 套件鎖檔不交給 Dependabot。CI 與部署 workflow 的測試 Python 版本直接從 Dockerfile 基底映像讀取，目前為 3.15。若 `.env` 明確設定 `CLOUDFLARED_VERSION`，它會覆寫 Compose 預設，值也應使用 `版本@sha256:已查證的digest`。本次不提供映像位元組完全一致的保證；固定的是基底內容與套件安裝輸入。

依據：[pip 雜湊驗證](https://pip.pypa.io/en/stable/topics/secure-installs/)、[pip-tools 鎖檔](https://pip-tools.pypa.io/en/stable/#using-hashes)、[Docker 固定基底 digest](https://docs.docker.com/build/building/best-practices/#pin-base-image-versions)。

## 本機 migration 與管理接口

### 同時寫入的限制

管理庫是單一 SQLite 檔案（rollback journal 模式），可能同時被管理頁（Docker 私人 API 或 `web` 指令）、CLI 匯入（例如 `import-tagging-results`、`sync-discord`）使用。所有連線都經 `db.connect`，遇到其他程序寫入時最多等待 30 秒，短暫鎖定不會直接失敗。

但 Windows 主機上的程序與 Docker Desktop 容器同時寫同一個檔案時，跨檔案共享的鎖定不可靠，等待無法保證不衝突。同一時間只讓一個程序寫入：大量匯入（例如 Agent 候選標籤）進行中時，管理頁先不要做寫入操作（接受／拒絕、儲存、刪除、發布）；也不要同時開兩個管理入口寫入。這也是不改用 WAL 模式的原因：WAL 需要共享記憶體，跨主機與容器的檔案共享無法正確運作。

新增 `meme_entries.publish_selected`（預設 0）、`public_id`（nullable、唯一）與 `attachments.public_original_url`。公開 ID 在第一次選擇發布時產生，取消再選不變；不把管理 key 或附件 ID 放進公開庫。原本 ready 不代表同意公開，亦不自動採用 Discord／r2_url 作原圖網址。

主庫 migration 已在副本重跑並比對原欄位，正式執行前備份為 `data/backups/anime_memes_before_sync_20261124_121103_842384.sqlite3`。原 1,076 筆內容不變，公開選取數 0，外鍵與完整性檢查通過。自行部署舊資料前先備份，再執行既有 init-db；公開模式不 migration。

新增管理端接口：

| 接口 | 行為 |
|---|---|
| GET /api/entries/{key}/publication | 查公開選擇、公開 ID、原圖網址 |
| PATCH 同路徑 | 僅接受 publish_selected 布林值與 public_original_url 字串／null；省略保留、空值清除網址 |
| POST /api/publication/bulk | 批次設定發布選取：`{"publish_selected":bool,"keys":[...]}` 或 `{"publish_selected":bool,"filter":{...}}`，可加 `dry_run`；單次上限 5000 筆 |
| POST /api/publication/check | 一致快照檢查，逐項 errors／warnings |
| POST /api/publication/export | 無參數或空物件；匯出至服務設定目錄，禁止指定任意路徑 |

管理庫結構版本以 `PRAGMA user_version` 記錄（`src/anime_memes/db.py` 的 `MIGRATIONS`；與公開庫的 SCHEMA_VERSION 無關）。`init_db` 只執行版本號大於目前值的遷移，每個遷移與版本號更新在同一交易內，失敗會回滾、版本不前進。版本 1 為基準（原本 `migrate_db` 的全部冪等步驟），舊資料庫 user_version 為 0，第一次啟動會自動套用並標記為 1，結構不變。新增遷移時只能在清單尾端追加下一個版本、不可修改已發布的遷移、需能在單一交易內完成（不自行 commit）。若資料庫版本高於程式支援的最新版，`init_db` 會丟出 RuntimeError 拒絕啟動，請更新程式。

### 時間格式規範

管理庫所有名稱以 `_at` 結尾的時間欄位（`updated_at`、`created_at`、`exported_at`、`last_success_at` 等，完整清單由 `db.iso_time_columns` 掃描 schema 取得），一律為 ISO 8601 UTC 毫秒格式 `YYYY-MM-DDTHH:MM:SS.sss+00:00`，例如 `2026-11-25T04:17:54.434+00:00`。統一格式後字串排序即等於時間排序（管理首頁依 `meme_entries.updated_at` 排序），過去混用 `2026-11-25 16:18:47`（SQLite `CURRENT_TIMESTAMP`）、`...T...+00:00` 與 `+08:00` 造成排序錯亂。

- 強制方式：遷移版本 2 把既有資料轉成標準格式（時間點不變，`+08:00` 會換算成 UTC），並為每個時間欄位建立 `trg_iso_<表>_<欄位>_ins`／`_upd` 觸發器（`AFTER INSERT`、`AFTER UPDATE OF 欄位`）。任何寫入者（含外部匯入工具、SCHEMA 的 `DEFAULT CURRENT_TIMESTAMP`）寫入非標準格式時，會被改寫為標準格式；無法解析的值保持原樣。SCHEMA 的 DEFAULT 不修改，以維持新舊庫結構一致。
- 程式寫入：SQL 用 `db.ISO_NOW_SQL`（`strftime('%Y-%m-%dT%H:%M:%f+00:00','now')`），Python 用 `db.utc_now_iso()`，兩者格式完全相同。比較時間請用同一格式或 `julianday()`。
- 外部工具寫入時應直接使用標準格式，不必依賴觸發器。
- 例外：`discord_messages.timestamp`、`timestamp_edited` 是 Discord 匯出的原始資料（offset 各異），Discord 同步會拿它們與新匯出的值比對來判斷訊息是否被編輯，轉換會讓下次同步誤判所有訊息被編輯，故保持原樣。`discord_sync_state.covered_through` 與 `export_runs.after_value`／`before_value` 是日期（YYYY-MM-DD）而非時間，也不轉換。

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

批次發布用 `publication bulk`（key 清單用 `--keys`／`--keys-file`，或用 `--status`／`--tag`／`--q`／`--favorite`／`--no-tags`／`--published` 篩選；未指定時為所有 ready 項目）。每個發布項目都需要 public ID，因此發布必須透過此指令或 API，不可直接改資料庫。先預覽再發布全部 ready 項目：

```powershell
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication bulk --selected yes --dry-run
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication bulk --selected yes
```

取消公開用 `--selected no`，清空原圖網址用 `--original-url ''`。取消選擇或刪除本機項目後，需再次匯出並啟用新版才會從公開搜尋移除；歷史版本與長快取媒體不會因此立即失效；需徹底移除時執行 `publication purge`（或管理頁「徹底清除」），刪除未保留的舊版本、只被它們使用的縮圖與不再需要的 R2 大圖，並依輸出網址清除 Cloudflare 快取。緊急情況仍可先停用公開入口。check／export 不合格 exit code 2；其他執行錯誤非零。API 的輸出根目錄由 create_app 的 release_root 或私人 runtime 的 MEMES_RELEASE_ROOT 設定；CLI 明確使用 --root。

匯出先用 SQLite backup API 取得一致管理快照，再將選定欄位輸入全新公開庫。公開縮圖優先由本地原圖以 480P 預設（長邊 ≤854、短邊 ≤480、WebP quality 78）產生，無可用原圖時退回既有縮圖；不放大，重建像素去掉 EXIF／ICC，未變動時可沿用已驗證成品。原始縮圖與原圖不被覆蓋。公開縮圖位於發布目錄的 `media/`，本機與 VPS 都由公開 Nginx 直接供應；原子寫入成品的權限明確設為 `0644`，讓產圖帳號 UID 10001 與 Nginx UID 101 可共用讀取，快取重用時也會修正缺少的跨帳號讀取權限。

若已設定 MEMES_R2_* 環境變數，匯出在取得快照前會先為已選取且無手動原圖網址的項目產生 720P WebP，以內容雜湊為物件鍵（HEAD 不存在才 PUT）上傳 R2，並寫回 attachments.r2_key／r2_url；任一上傳失敗則不建立版本，錯誤寫入報告。公開庫 original_url 為手動網址，否則為 R2 網址，皆無則僅縮圖。公開端不代理外部圖片，也不執行縮圖。

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

公開資料結構目前為第 4 版。第 2 版在 entries 新增 `thumbnail_width`、`thumbnail_height`（實際縮圖尺寸），前端據此在圖片載入前預留版面，並依高度做由左到右的瀑布流排列；第 3 版（2026-11-25）移除 `ocr_text`，OCR 不再進入公開快照，發布檢查也不再掃描 OCR 文字。公開服務只讀取相同結構版本的快照，升級程式後舊版本會被拒絕（公開 API 健康檢查失敗），須重新匯出並啟用一次；第 4 版新增 `created_at`（管理庫建立時間，供「最近新增」排序，API 會回傳）與 `home_random`（1＝可進首頁隨機，匯出時依 `MEMES_HOME_EXCLUDED_TAGS` 計算，不回傳）並建立 `idx_public_created` 索引，**升級後需重新匯出並啟用一次**；R2 大圖以內容雜湊沿用，不會重傳。

同步至目標發布目錄時，先完整放好 media 與 versions，最後在**目標主機**驗證並啟用，勿把上傳中的檔案直接指定成目前版本，也不要同步覆寫 current.json。

```powershell
.venv\Scripts\python.exe -m src.anime_memes.runtime validate --root data/releases VERSION
.venv\Scripts\python.exe -m src.anime_memes.runtime activate --root data/releases VERSION
.venv\Scripts\python.exe -m src.anime_memes.runtime rollback --root data/releases OLD_VERSION
```

validate 核對 schema／搜尋規則版本、完整性、白名單、ID／網址、筆數及雜湊。activate／rollback 都只在成功驗證後原子切換 current.json。每個請求固定一個版本並開啟唯讀交易，完成後關閉；無需全庫 dict 或重啟服務。Windows 短暫檔案分享衝突有最多約一秒的 replace 重試，失敗保留原指標。

管理資料／報告與公開資料分開；Nginx **只掛載 media**，不可供應 versions、reports 或 DB。對外 API 每個回應帶版本號，分頁可用 `version=VERSION` 固定版本；已移除版本回傳 410 version_expired。沒有啟用版本時 readiness 503、liveness 200。

版本清理可在服務執行中進行：公開 API 每個請求只讀目前啟用版，其他版本一律回 410，因此刪掉沒被保留的舊版不影響服務：

```powershell
.venv\Scripts\python.exe -m src.anime_memes.runtime prune --root data/releases
```

保留最新三個版本及目前啟用版本，其餘版本與不再引用的媒體才刪除。檢查報告另行保留。VPS 的受限發布指令會在啟用成功後自動清理；本機不自動清理，也不建立排程。

### 推送到 VPS

私人管理端只留在本機；VPS 只跑公開 profile。本機匯出後可把單一版本打包成 tar.gz，傳到 VPS 安裝：

```powershell
.venv\Scripts\python.exe -m src.anime_memes.runtime bundle --root data/releases --output bundle.tar.gz VERSION
.venv\Scripts\python.exe -m src.anime_memes.runtime install-bundle --root /releases [--activate] /bundle.tar.gz
```

bundle 先驗證版本，完整打包 `versions/<V>/manifest.json`、`public.sqlite3`；未指定 `--inventory REMOTE.json` 時帶入該版本全部縮圖，指定時只帶入遠端清單缺少的 `media/<sha256>.webp`。清單由 VPS 的 `runtime media-inventory --root /releases` 產生，實際核對內容 SHA-256，略過損壞或符號連結檔案；不含 reports 與 current.json。增量包依賴該 VPS 已有的媒體，不能當作獨立完整備份。

install-bundle 在寫入任何檔案前先檢查全部成員（僅允許白名單內的一般檔案、單一版本、無重複、無絕對路徑／`..`／連結），解壓到 `.incoming-*` 暫存目錄，已存在的版本拒絕覆寫，已存在的媒體需雜湊與檔名相符才略過，最後對新 DB、完整媒體清單及所有實際檔案呼叫 validate；驗證失敗會移除剛安裝的版本資料夾，保留目前啟用指標。比對後遠端被清理造成缺檔時同樣阻擋啟用，重新取得清單並打包即可；既有物件雜湊錯誤則中止，須先查明損壞原因。公開檔案權限設為 `0644`，媒體目錄為 `0755`。只有加上 `--activate` 才會切換 current.json，且一律經 `releases.activate` 原子寫入，不會被直接覆寫。

`scripts/push_release.ps1` 預設先透過 SSH 以唯讀掛載取得遠端縮圖清單，再本機增量打包、scp、ssh 以公開映像執行 install-bundle、清除暫存檔，每步檢查結束碼：

```powershell
.\scripts\push_release.ps1 -Version VERSION -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases [-Activate] [-Full] [-DryRun]
```

前提：VPS 已有支援 `media-inventory` 的新版公開映像（預設 `memedatabase-public-api`）與發布目錄，且該目錄允許 UID 10001 寫入；舊映像需先更新，或以 `-Full` 退回完整傳輸。清單取得或解析失敗時停止，不默默改為全量上傳。SSH 認證使用你既有的 ssh 設定，腳本不處理密碼或 token。`-DryRun` 只印出命令，不執行任何步驟。

新增或換圖以內容雜湊決定是否傳輸，僅修改文字／標籤時只更新 DB 與 manifest。刪除或取消公開直接反映在新 DB，啟用後從搜尋移除；不以 SQL 差異逐筆修改線上庫，也不在安裝時刪除舊版媒體。舊檔依既有 `-Prune` 保留最新三版及目前啟用版後清理；徹底撤回仍需另外處理 R2 與 CDN 快取。

完整的 VPS 首次部署、Cloudflare Tunnel 設定與日常維運見 [VPS 部署手冊](vps-deployment.md)。

私人管理頁已整合上述增量流程：`GET /api/publication/vps` 取得連線設定、最近工作與可選版本，`PUT /api/publication/vps/config` 儲存 `ssh_host`／`release_root`，`POST /api/publication/vps` 以 `{version, activate}` 啟動背景發布並立即回傳 202。設定不接受指令或金鑰，遠端僅執行固定的 Docker 發布命令；輪詢不經公開站。重新整理可恢復進度與結果，本機程序重新啟動則標示舊工作結果待確認，不自動重試。權限、專用 SSH 目錄及使用步驟見部署手冊「從本機發布管理操作」。

## 公開搜尋契約

### 瀏覽器個人館藏與手動投稿

公開搜尋 API 仍為唯讀，沒有新增投稿或個人圖片寫入端點。「我的梗圖」與私人管理庫互不相通，圖片 Blob 及欄位只存在目前網站 origin 的 IndexedDB `memedatabase-personal`（版本 1、`memes` store）；本地搜尋不呼叫公開搜尋 API。不同裝置、瀏覽器、網域、協定或 port 不共用資料，例如本機測試站與正式網域需以備份自行搬移。這不代表整個網站離線可用，載入網頁與查詢公開館藏仍需網路。

圖片只接受 JPEG／PNG／WebP／GIF，驗證大小、檔頭，支援時另以瀏覽器解碼確認；每張上限 10 MB、500 筆、圖片總量 60 MB，並受實際瀏覽器空間限制。保存成功以 IndexedDB transaction complete 為準；額滿或交易失敗不回報成功。請求持續儲存只能降低自動回收機會，無法防止使用者清除網站資料或裝置故障。

備份是 JSON，`format: memedatabase-personal`、`version: 1`，內含欄位與 base64 圖片，上限 100 MB。匯入先驗證整份備份及所有圖片，成功後以單一交易新增，重複 ID 略過，不覆寫既有資料；格式不支援、任一圖片損壞或交易失敗都不做部分匯入。這是第一版格式，目前支援 v1；未來有格式調整時需明確新增相容轉換，不直接清空 IndexedDB，未知版本則拒絕匯入並保留使用者原檔。備份不含公開收藏或公開 DB。

表單欄位由 `public_ui/meme-form.js` 共用，樣式共用 `web_static/catalog.css`，管理頁額外提供工作狀態欄位。個人卡片、來源及投稿文字以文字節點／表單值顯示，不將使用者輸入當成 HTML。投稿只建立固定收件人 `meme@jwander.net` 的 mailto 草稿，另提供複製格式與下載圖片；超過連結長度限制時提示手動貼上全文。UI 不自動附檔、不代寄、不聲稱已寄達。信箱仍須處理垃圾信與不可信附件；此流程減少公開投稿 API 的攻擊面，但不是零風險保證。核准後仍由管理者在私人端匯入／建檔，再依現有發布流程上架。

| 接口 | 用途 |
|---|---|
| GET /api/v1/entries | q、tag、sort、seed、page、page_size、version |
| GET /api/v1/entries/{id} | 單筆，僅接受 version |
| GET /api/v1/meta | 正式標籤與數量，僅接受 version |
| GET /health/live | 程序可回應 |
| GET /health/ready | 已啟用且 schema 相容 |

列表預設每頁 24、最多 60，page 1–10000；sort 為 relevance、updated、created（最近新增，依建立時間）、title、random。`sort=random` 必須帶 `seed`（1–32 字元，僅 `A-Za-z0-9_-`，否則 400），順序為 `sha256(seed:id)`，同 seed 分頁穩定不重複；沒有 q、tag、ids 時只取 `home_random=1`，排除標籤預設為 `content_sexual_innuendo`、`content_perverted`（環境變數 `MEMES_HOME_EXCLUDED_TAGS` 以逗號分隔覆寫），有任何篩選則不排除。本機瀏覽 `/api/v1/entries` 語意相同（沒有 q、tag、favorite 時排除，依 entry_tags 的 tag_id 判斷）；管理 API `/api/entries` 新增 `sort=created`，random 維持原行為、不做首頁排除。q 最長 256 字／16 詞。有查詢預設相關度，否則最近更新；保留繁簡、標點、多詞、標籤／別名搜尋規則，並支援 `-排除`、`作品:詞` 欄位限定、引號片語與 `field` 搜尋範圍參數（work／speaker／quote／title／tag／alias，見 [DB 與搜尋規格 5.4](database-search-spec.md)）。不接受 status、favorite、missing_image 或其他管理參數。

列表回傳 `{version, items, total, page, page_size}`；單筆 `{version, item}`。item 欄位為 id、display_title、resolved_title、speaker、anime_title、quote_text、tags、search_aliases、thumbnail_url、original_url、updated_at、created_at；OCR 不參與搜尋，也不寫入公開快照（第 3 版起）。未指定版本的 API 使用 `no-store`，明確指定版本的成功回應使用一年 immutable 快取；錯誤不長期快取。圖片雜湊 URL 同樣使用一年 immutable 快取。

公開頁的收藏只保存在訪客瀏覽器，以 `ids` 查詢最新仍可用的資料；單次最多 200 個 ID。收藏列表顯示最近 200 張，並說明顯示上限、超出範圍的較早收藏數量，以及已下架數量；較早紀錄不自動刪除。本機 `/browse` 透過 `favorite=1` 查詢管理庫收藏，不使用這個顯示上限。

錯誤格式為 `{error: {code, message}}`，涵蓋 400 invalid_request、404 not_found、405 method_not_allowed、410 version_expired、503 not_ready。Nginx 限流回傳 429；其預設 HTML 錯誤頁不遵循 API JSON 格式，前端須先判斷 HTTP status。

公開版只提供網站需要的 API，不提供 Agent 專用路徑、token 平台或 MCP。匿名 HTTP 搜尋仍可能被自動化呼叫，沒有宣稱能辨別或阻止所有機器人。

## Docker／Nginx 本機啟動

首次先產生並啟用發布包，公開 API 才會 ready；私人版可先啟動管理。Linux bind mount 的資料權限須允許容器 UID 10001：私人資料需可寫，公開 DB／媒體需可讀。既有 DB 的 Windows 媒體路徑透過設定的掛載根目錄映射 basename，不改寫原紀錄；原始資料、模型與秘密不打進映像。

```powershell
# 同一個 PowerShell 工作階段使用隔離測試資料
$env:PRIVATE_DATA_ROOT = './data/frontend-qa-1124/private'
$env:RELEASE_ROOT = './data/frontend-qa-1124/releases'
docker compose -p memedatabase-frontend-qa --profile private up -d --build --wait
# 在私人頁完成首次啟用後執行
docker compose -p memedatabase-frontend-qa --profile public up -d --build --wait
```

1. 開啟[私人管理頁](http://localhost:3636)，手動新增測試圖，填入作品「刃牙」、角色「烈海王」、台詞「復！活！」及標籤「復活」。在詳情勾選「納入公開館藏」並儲存。原圖網址可留空以測試僅發布縮圖。
2. 在「發布管理」按檢查。逐筆問題可點管理 key 開啟修正。匯出後確認版本與筆數，再按「啟用此版」。選取零筆時，啟用後公開搜尋會變空。
3. 啟動公開 profile，開啟[公開搜尋頁](http://localhost:3637)。搜尋「復活」、套用標籤、開啟詳情、下載縮圖，將頁面連結貼到新分頁驗證。原圖為選填的外站資源，剪貼簿或下載受限時可開啟原圖或下載本站縮圖。
4. 在私人頁取消選取後再檢查、匯出，此時重新搜尋仍可找到該圖。啟用新版，再按公開頁「搜尋」，確認結果更新。後續換版可直接在頁面操作。
5. 結束測試可執行 `docker compose -p memedatabase-frontend-qa --profile public --profile private down`。測試館藏保留於上述主機目錄。

Nginx 代理 API、健康檢查及 media，停用目錄列表；預設每 IP 2 次／秒、burst 10，SEARCH_RATE／SEARCH_BURST 可覆寫。有關鍵字（`q`）的搜尋另外限制：每 IP 1 次／秒、burst 8，全站同時最多 3 個，超過回 429（SEARCH_KEYWORD_RATE／SEARCH_KEYWORD_BURST／SEARCH_KEYWORD_CONCURRENCY 可覆寫，預設值也寫在 Nginx 映像裡）。未信任任意 X-Forwarded-For；公開設定已從本機／私有網段的 Cloudflare Tunnel 來源讀取 `CF-Connecting-IP`，其安全前提是公開 port 只綁 localhost，外部請求僅透過受控 Tunnel。若改成其他入口或信任來源，須重新檢查真實 IP 與限流設定。

| 環境變數 | 預設／用途 |
|---|---|
| PRIVATE_DATA_ROOT | ./data，私人完整資料掛載 |
| RELEASE_ROOT | ./data/releases，發布資料掛載 |
| PRIVATE_PORT／PUBLIC_PORT | 3636／3637，主機 loopback port（`web` 指令直接執行時預設 3638） |
| SEARCH_RATE／SEARCH_BURST | 2r/s／10 |
| SEARCH_KEYWORD_RATE／SEARCH_KEYWORD_BURST／SEARCH_KEYWORD_CONCURRENCY | 1r/s／8／3，只計有關鍵字的搜尋 |
| PRIVATE_URL | http://127.0.0.1:3636，MCP 回傳給呼叫端的媒體基址 |
| MEMES_MCP_TOKEN | 無預設，至少 32 ASCII 字元、無空白 |

Compose 預設公開後端上限 192 MB、Nginx 64 MB、私人後端 384 MB、MCP 192 MB；這些是可調整的限制，並非測得容量保證。各 Python 程序單程序 4 Waitress threads，Nginx 1 worker；Docker 日誌 5 MB × 3 份，含健康檢查與 unless-stopped。不開管理面板；沒有直接對外 TLS 配置，VPS 的 HTTPS／Tunnel 留待部署階段。

資料更新不重建映像。程式更新使用 `up -d --build`；回復映像與相容資料版本應一起處理。一般 stop／down 不刪 bind mount 資料。

## 私人選用 MCP

私人管理頁 `/api/publication/` 的 Nginx 上游回應等待為 600 秒（10 分鐘），涵蓋檢查、匯出、啟用與清理；一般 API 與公開服務維持原設定。這是兩次讀取上游資料之間的等待上限，並非保證匯出能在 10 分鐘內完成。收到 504 不代表後端工作已取消，請先查看日誌與發布結果，避免連續重試。

發布檢查與匯出會重用圖片驗證及轉檔結果：每次仍檢查目前勾選公開的所有資料、狀態與網址，並讀取來源圖片計算 SHA-256；來源內容、檔案資訊、Pillow 版本與轉檔規格相同時，省略重複解碼，重用 480P 縮圖與 720P 大圖。只修改文字或標籤也會重新產生公開資料快照，但無須重新編碼未變動的圖片。縮圖與 720P 成品在重用前會核對內容雜湊；發布包仍完整驗證資料庫與媒體清單。

快取位於管理資料庫同一目錄的 `.publication-cache.sqlite3`，屬私人、可丟棄的加速資料，不隨公開發布包匯出；刪除後會重新驗圖與產圖。僅儲存成功的圖片驗證結果；快取讀寫失敗會記錄警告並退回正常處理。若要強制逐張解碼，可執行：

```powershell
.venv\Scripts\python.exe -m src.anime_memes --db data/anime_memes.sqlite3 publication check --full
```

首次檢查、首次匯出、大量新圖或 R2 上傳仍可能超過 10 分鐘，長作業可使用 CLI。目前仍為同步請求，尚無背景工作或即時進度。`docker compose -p memedatabase logs --tail 100 private-api private-nginx` 可查看檢查起訖、耗時、解碼／重用數及 Nginx 請求與錯誤；匯出報告另外保存在 releases 的 `reports/*.json`。這些操作日誌不等同具使用者身分的完整異動稽核紀錄。

R2 物件使用圖片內容 SHA-256 命名；已成功記錄於管理庫的大圖會跳過上傳，未記錄時也先 HEAD 確認物件是否存在。一般重試不會產生同內容的另一個物件，但連線中斷或重試可能增加請求／傳輸。同時只允許一個匯出持有 `.export.lock`；不要在仍有工作執行時手動移除此鎖。每張上傳成功後即提交紀錄，重新匯出可沿用已完成部分，但可能建立另一個發布版本；匯出本身不會啟用版本。

```powershell
# token 請自行安全產生並透過環境注入，不提交實際值。
$env:MEMES_MCP_TOKEN = '<至少32字元的隨機ASCII值>'
docker compose --profile private --profile mcp up -d --build
```

預設 `http://127.0.0.1:8766/mcp`，使用 `Authorization: Bearer TOKEN`，無狀態 Streamable HTTP。僅 search_memes(query, limit=5，最多20, field=None) 與 get_meme(id)，查 ready 資料，共用本機搜尋。metadata 有回傳數量與文字長度上限，不回傳 notes、OCR、來源訊息或檔案路徑。未提供／錯誤 token 回 401，錯誤 Host／Origin 回 403，不存在寫入工具。

非 Docker 可 `python -m src.anime_memes.private_mcp`；預設 loopback，依環境設定 MEMES_DB、MEMES_PRIVATE_URL。自行換 port／網域時也必須設定精確的 MEMES_MCP_HOSTS／MEMES_MCP_ORIGINS 清單，勿用萬用允許值。私人管理端沒有新增遠端登入系統，因此 loopback 不可直接改成公開曝光。

## 驗證紀錄與限制

2026-11-26 更新建置鎖定後，Python 287 項測試執行完成（285 通過、Docker／GPU 選用項目 2 項略過），Node 狀態測試 44 項通過。另行啟用真實 Docker 整合測試通過，涵蓋三種 Python 與兩種 Nginx 映像、私人 MCP 及發布換版。三份鎖檔預設重跑後 SHA-256 相同；隔離安裝預檢確認未同步的直接版本、新增但未鎖定的依賴與錯誤套件雜湊均被拒絕。容器測試環境為 Linux／amd64，不代表已驗證其他架構、GPU、正式 VPS 或本輪真實瀏覽器流程。

測試涵蓋既有功能、公開欄位隔離、整批阻擋、縮圖／路徑、版本固定與並行切換、損壞回復、唯讀連線、MCP 協定與權限。容器已實際建置並驗證 Nginx 設定、管理靜態頁與圖片、公開 API、縮圖快取與限流、公開映像無 Pillow／OCR／MCP、掛載 DB 寫入失敗。

2026-11-26 真實 Docker 整合測試 `tests/test_containers.py` 已通過，耗時約 108 秒：臨時管理庫的 API 修改及匯出、容器外啟用新版、公開 API 不重啟即讀新版、指定舊版、rollback、強制重建公開容器後版本保留，以及私人 MCP 握手／搜尋皆成功。測試自行建立臨時資料並在 finally 關閉隔離 Compose 專案，不碰正式館藏。重跑方式：

```powershell
$env:RUN_CONTAINER_TESTS = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_containers.py -v
Remove-Item Env:RUN_CONTAINER_TESTS
```

需可存取 Docker daemon、套件來源與 loopback，測試使用主機 port 18865、18875、8766，請先確定未被其他程序占用。一般回歸不啟用此環境變數，GPU smoke test 同樣需要另行 opt-in。容器測試時公開 API 約 27 MiB、Nginx 約 4 MiB，僅小型 fixture 的瞬間值，非正式負載或容量上限。

2026-11-24 使用實際館藏副本中的 981 筆合格候選，暖查詢中位數約 11–14 ms；複製欄位擴充到 9,810 筆約 122–133 ms。Windows 測量程序包含 Pillow／匯出模組，RSS 約 42–45 MB，不代表公開容器 RAM。這些是歷史基準，不代表目前館藏筆數。標籤查詢計畫使用 idx_public_tag 覆蓋索引，任意文字仍掃描，不宣稱全文索引加速或 VPS 容量。

VPS 首次部署、Cloudflare Tunnel／HTTPS、推送與回復已有[部署手冊](vps-deployment.md)及 `scripts/push_release.ps1`；實際主機的資源、外部存取與還原驗收需另行記錄，不能從本機容器測試推定。遠端管理登入、PostgreSQL 與 OCR Docker 不屬於目前交付；管理庫與原圖的完整備份／異地保存仍待補齊。
