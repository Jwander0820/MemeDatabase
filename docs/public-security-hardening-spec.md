# 公開網域安全強化與維運評估規格

- 版本：1.0
- 日期：2026-11-26
- 狀態：待維運與開發評估（本文件僅定義評估項目、技術方案與驗收條件，尚未變更現有系統）
- 關聯文件：[VPS 部署手冊](vps-deployment.md)、[雙版本後端與發布操作](backend-refactor.md)、[整體規格](personal-meme-platform-spec.md)

---

## 1. 目的與真實架構邊界（評估前提）

本規格書盤點專案將公開搜尋站（`meme.jwander.net`）與公開大圖 CDN（`cdn.jwander.net`）對外開放時，在**反向代理、容器網路、API 資源保護、雲端快取計費、VPS 部署權限及公開儲存庫衛生**各層面的風險與具體修正規格，供維運與審查人員依實際主機條件與維護成本決定施作範圍。

為避免過度防護或誤判風險，評估時請以此真實架構邊界為準：

1. **公私環境實體分離**：
   - **VPS 公開端**：僅啟動 `public`（`public-nginx`、`public-api`）與 `tunnel`（`cloudflared`）profile，主機不開放連入 port（僅開 SSH），對外僅提供唯讀搜尋 API 與已脫敏的 WebP 縮圖。公開容器映像不含管理介面、寫入路由、Pillow、OCR、MCP、`.env` 或主資料庫。
   - **本機管理端**：`private`（port `3636`／原生 `3638`）與 `mcp`（port `8766`）**僅在管理者個人電腦本機 loopback（`127.0.0.1`）執行，不部署上 VPS，也不接上公開 Tunnel**。本機管理端不設帳號登入屬單機工具設計，並非公開網域暴露面。
2. **現行已具備的防護基線（無須重做）**：
   - **無常見注入漏洞**：公開與私人 SQL 查詢均使用參數化綁定（無 SQLi），公開庫以 `?mode=ro` 與 `PRAGMA query_only=ON` 開啟；無伺服器端模板渲染（無 SSTI）；公開服務無任何子程序呼叫（無 Command Injection）；公開後端不對外發起網路請求（無公開面 SSRF）。
   - **前端與新增個人館藏安全**：公開前端與「我的梗圖」（`personal-store.js`、`personal-ui.js`）均以 `textContent` 建立 DOM（無 `innerHTML`），搭配嚴格 CSP（`script-src 'self'; object-src 'none'`）；瀏覽器個人梗圖僅存於訪客端 IndexedDB，不寫入伺服器，且已具備圖片魔術數字驗證、4,000 萬像素上限、容量上限、`source_page` 協定檢查與 `mailto:` URI 編碼。
   - **資料與影像脫敏**：公開快照僅含白名單欄位（不含 `ocr_text`、`notes`、Discord 原始訊息與本地路徑），公開縮圖與 R2 大圖轉檔時均重建像素去除 EXIF／ICC 中繼資料。
   - **容器與供應鏈硬化**：非 root 執行（UID 10001／101）、`read_only: true`、`cap_drop: [ALL]`、`no-new-privileges:true`、基底映像固定 `@sha256` digest、Python 依賴以 `--require-hashes --only-binary=:all:` 安裝。

---

## 2. 評估項目總覽矩陣

各項目按真實影響與施作成本分為：**P0（建議上線前或優先處理）**、**P1（建議近期排程優化）**、**P2（維運選配／防禦深度強化）**。

| 編號 | 項目名稱 | 影響層級 | 真實風險說明（不誇大） | 變更位置 | 改動量 | 建議優先序 |
|---|---|---|---|---|---|---|
| **SEC-01** | 公開 Nginx 封鎖對外 `/health/` 路徑 | 公開站可用性 | 外部可直接打 `/health/ready` 繞過 Nginx 與 Cloudflare API 限流，每次請求皆開關 SQLite 並跑 6 次 schema 檢查，易塞滿 4 條 Waitress 執行緒導致 502／504 | `deploy/nginx-public.conf.template` | 極小 | **P0** |
| **SEC-02** | 公開 API 僅允許查詢「當下啟用版本」＋緊急清空舊版參數 | 下架即時性 | 目前帶舊 `?version=` 仍可查到 VPS 尚未 prune 的舊快照（預設留 3 版）；改為只允許啟用版後，換版即刻封鎖舊版 API 查詢，保留磁碟舊檔僅供本機 rollback | `src/anime_memes/public_web.py`、`releases.py`、`runtime.py`、`scripts/push_release.ps1` | 小 | **P0** |
| **HYG-01** | `.gitignore` 補排除 `.claude/launch.json` 與 `.codex/` | 公開 Git 倉 | `.claude/launch.json` 含本機使用者帳號暫存路徑且目前未被忽略（尚未被提交）；補入可防誤執行 `git add .` 推上公開 GitHub | `.gitignore` | 極小 | **P0** |
| **OPS-01** | `meme.jwander.net` Cloudflare 邊緣防護與限流範圍核對 | 公開站邊緣 | HSTS、TLS 1.2+ 與邊緣限流依賴 Cloudflare 控制台設定；需確認實際已開啟且限流規則涵蓋範圍正確 | Cloudflare 控制台、`docs/vps-deployment.md` | 設定核對 | **P0** |
| **SEC-05** | 首頁「常用圖＋隨機骰圖」合併快取與 `validate_schema` 快取 | 可用性與體驗 | 有 5 張常用圖的訪客進首頁發 7 次請求，再按一次「骰一張」又重發 6 次（共 13 次 > burst 10），正常使用即觸發 `429` 且穿透 CDN | `public_ui/gallery.js`、`src/anime_memes/public_web.py` | 小 | **P1** |
| **SEC-03** | Compose 網路分段與 `public-api` 內部斷網（`internal: true`） | 容器隔離 | 目前所有服務共用預設 bridge；分段後 `cloudflared` 只能連 `public-nginx`，且 `public-api` 核心層禁止對外連網 | `compose.yaml`、`tests/test_containers.py` | 小 | **P1** |
| **SEC-04** | Nginx IPv6 `/64` 歸戶限流、擴大狀態區與明確逾時 | 公開站抗濫用 | 現以單一 128-bit IPv6 計數，擁有 `/64` 子網者可輪換位址繞過限流並填滿 `1m` 記憶體區；另補齊上游與連線短逾時 | `deploy/nginx-public.conf.template` | 小 | **P1** |
| **OPS-02** | R2 自訂網域（`cdn.jwander.net`）防快取穿透與停用 `r2.dev` | 雲端費用控制 | 隨機 Query String（`?r=1`）預設可能穿透 CDN 打向 R2 累積 Class B 讀取次數；於 Cloudflare 設定忽略查詢字串即可阻斷 | Cloudflare 控制台、`docs/vps-deployment.md` | 設定核對 | **P1** |
| **HYG-02** | `examples/` 與歷史文件路徑／Discord ID 去識別化 | 公開 Git 倉 | 已提交檔案含 2 處專案資料夾路徑（不含個人帳號名）與 2 個範例附件編號；改為通用佔位符較整潔 | `examples/*`、`docs/archive/vision-tagging-stage-spec.md` | 極小 | **P1** |
| **OPS-03** | VPS 部署 SSH 金鑰改用受限指令 Wrapper（脫離 `docker` 群組） | VPS 共機隔離 | 外部無法連入本機管理端；僅防範本機私鑰檔案外流時，避免該金鑰等同整台共機 VPS 的 `root` 權限 | VPS 主機設定、`docs/vps-deployment.md` | 中（維運） | **P2（選配）** |
| **SEC-06** | 公開 Nginx `/assets/` 收緊、CSP 網域收斂與 `500` JSON 回應 | 防禦深度 | 無直接漏洞，屬規格一致性與最小暴露面微調 | `deploy/`、`public_web.py` | 極小 | **P2（選配）** |
| **OPS-04** | 投稿信箱 `meme@jwander.net` 郵件防偽與附件開啟規範 | 日常維運安全 | 公開 `mailto:` 信箱必收垃圾信或偽裝附件；網站不收檔無主機風險，僅需注意信箱端防偽與審稿習慣 | 網域 DNS／日常審稿流程 | 流程規範 | **P2（提醒）** |
| **HYG-03** | 本機管理端與離線 CLI 防禦深度補強 | 本機私有環境 | 僅在匯入不可信外部匯出檔或直跑原生 `web` 時相關，完全不影響公開網域 | `web_static/app-detail.js`、`web.py`、`web_import.py`、`attachments.py`、`media.py` | 小 | **P2（選配）** |

---

## 3. 第一類：公開服務與容器架構規格（程式碼與設定檔變更）

### SEC-01（P0）：公開 Nginx 封鎖外部存取 `/health/`

- **現況與位置**：
  - `deploy/nginx-public.conf.template` 將 `location /health/` 直接 `proxy_pass http://public-api:8765;`，未設 `limit_req`，亦未限制來源 IP。
  - `docs/vps-deployment.md` 3.5 節建議的 Cloudflare 邊緣限流只針對 `/api/v1/`。
- **真實風險**：
  - `/health/live` 與 `/health/ready` 僅供 Docker 健康檢查與 VPS 本機維運確認（`curl http://127.0.0.1:3637/health/ready`），外部訪客與前端完全不需要使用。
  - 對外開放使外部請求可繞過所有限流直達只有 4 條執行緒的 `public-api`，且 `/health/ready` 每次都會開啟 SQLite 並執行 6 次結構驗證查詢，低頻率併發即可拖慢或卡死公開搜尋。
- **規格變更**：
  1. 在 `deploy/nginx-public.conf.template` 的 `location /health/` 限制僅本機來源可存取，外部來源一律回 `404`（不暴露端點存在）：
     - 由於 `set_real_ip_from`＋`real_ip_header CF-Connecting-IP` 已啟用：經由 Cloudflare Tunnel 進入的所有外部請求，其 `$remote_addr` 必定被 Cloudflare 邊緣覆寫為訪客真實公網 IP；而容器內部健康檢查（`127.0.0.1:8080`）與 VPS 主機透過 loopback 轉發的檢查不帶 `CF-Connecting-IP`，`$remote_addr` 為 `127.0.0.1`／`::1` 或 Docker 內部橋接位址。
     - **注意**：為避免無帶標頭的容器網路請求與 Tunnel 混淆，最明確且零歧義的做法是在 `nginx-public.conf.template` 將對外 `listen 8080;` 的 `location /health/` 直接拿掉（走預設 `return 404;`），另開一個僅監聽容器本機 `listen 127.0.0.1:8081;`（或允許本機並確認 `compose.yaml` 檢查路徑）的內部健康檢查 server 區塊；若維持同一 port `8080` 供主機 `127.0.0.1:3637/health/ready` 測試，則以 `map $http_cf_connecting_ip $is_external_tunnel { "" 0; default 1; }` 判斷：凡帶有 `CF-Connecting-IP`（即來自 Cloudflare Tunnel 的公網請求）一律 `return 404;`。
- **影響評估**：公開前端不使用 `/health/`，零功能影響；Docker `healthcheck` 與 VPS 本機 `curl http://127.0.0.1:3637/health/ready` 維持原操作方式不變。
- **驗收條件**：
  - 本機／容器直連 `/health/live` 與 `/health/ready` 回 `200`（未啟用版本時 `ready` 回 `503`）。
  - 模擬帶 `CF-Connecting-IP` 標頭（或實際經公開網域 `https://meme.jwander.net/health/ready`）請求時回 `404`，且請求不會轉送到 `public-api`。

---

### SEC-02（P0）：公開 API 僅允許查詢「當下啟用版本」＋支援緊急清空舊版

- **現況與位置**：
  - 前端第一次載入不帶 `version`，取得當下版本號後，後續翻頁、查標籤與開詳情會帶 `?version=<版本號>` 以啟用 1 年 `immutable` CDN 快取並確保翻頁期間順序穩定。
  - 但 `src/anime_memes/public_web.py`（第 26–29 行）未核對請求的 `version` 是否等於 `current_version(root)`；只要 `versions/<version>/` 尚在 VPS 磁碟上，任何舊版號皆可查詢。
  - `src/anime_memes/releases.py` 的 `prune()` 固定保留最新 3 版＋啟用版，`runtime.py` 與 `scripts/push_release.ps1` 無法指定只留啟用版。
- **真實風險**：
  - 一般換版時沒問題；但若發現某張已發布梗圖含有未注意到的個資、錯誤內容或版權爭議而取消公開並推新版，只要舊版本目錄還在 VPS 上，知道舊版本號的人仍可透過 API 查出舊文字資料，且舊縮圖檔案也不會被 `prune` 刪除。
- **規格變更**：
  1. **公開 API 鎖定當下啟用版本（`src/anime_memes/public_web.py`）**：
     - 每次請求先讀取 `active = current_version(root)`。
     - 若請求帶有 `?version=` 且 `version_name(requested) != active`，直接拋出 `ReleaseMissing("版本不存在或已過期，請重新搜尋")`（回傳既有的 `410 version_expired` 與 `Cache-Control: no-store`）。
     - 磁碟上的舊版本資料夾依然保留給維運執行 `rollback` 使用，但對外 API **永遠只能查當下啟用版本**。
  2. **清理指令支援 `--keep-recent` 參數（`releases.py`、`runtime.py`、`scripts/push_release.ps1`）**：
     - `releases.prune(root, *, service_stopped: bool, keep_recent: int = 3)` 預設維持 `3`（日常發布行為完全不變），允許傳入 `0`～`20`。
     - `runtime.py prune` 新增 `--keep-recent`（預設 `3`）；`scripts/push_release.ps1` 新增 `-KeepRecent <int>`（預設 `3`）。緊急撤回時可指定 `0`，立即移除所有非啟用舊版及其獨佔的 `/media/*.webp` 縮圖。
- **影響評估**：
  - 公開前端（`gallery.js` 與 `gallery-detail.js`）原本就已完整實作 `410` 處理（顯示「館藏版本已過期」與「從最新館藏重新搜尋」按鈕），只有在管理者「剛好切換新版的那一瞬間」正在滑舊頁面的訪客會看到重新載入提示，日常瀏覽與 Cloudflare 1 年快取完全不受影響。
- **驗收條件**：
  - 啟用新版本 `V2` 後，`GET /api/v1/entries?version=V1`、`/api/v1/entries/<id>?version=V1`、`/api/v1/meta?version=V1` 均立即回傳 `410`（即使 `V1` 仍在 `versions/` 目錄中）。
  - 執行 `rollback` 切回 `V1` 後，`?version=V1` 恢復回傳 `200`，`?version=V2` 改回 `410`。
  - `prune --service-stopped --keep-recent 0` 僅保留目前啟用版本及其引用之縮圖，其餘舊版與孤兒縮圖均被刪除。

---

### SEC-03（P1）：Docker Compose 網路分段與 `public-api` 斷網封裝

- **現況與位置**：`compose.yaml` 未宣告 `networks`，所有啟動的容器位在同一個預設 bridge 網路，`public-api` 亦具備預設對外連網能力。
- **真實風險**：
  - `cloudflared` 使用遠端 Token 模式（路由目標由 Cloudflare 雲端控制台決定）。若控制台誤填目標位址（例如填成 `http://public-api:8765`），流量會繞過 `public-nginx` 的限流與安全標頭；若有人在跑著 `private` 的機器上同時啟動 `tunnel`，網路層甚至可直通 `private-nginx`／`private-api`。
  - `public-api` 只需讀本地唯讀檔案，無任何對外連線需求；維持可連網只是多餘的暴露面。
- **規格變更**：
  - 於 `compose.yaml` 定義三個網路：
    1. `public_edge`（一般 bridge）：掛載於 `cloudflared`、`public-nginx`。
    2. `public_internal`（設定 `internal: true`，無預設閘道、禁止連外）：掛載於 `public-nginx`、`public-api`。
    3. `private_net`（一般 bridge，供 R2 與 VPS SSH 連出）：掛載於 `private-nginx`、`private-api`、`private-mcp`。
- **影響評估**：不改變任何對外或主機 loopback port（`3636`、`3637`、`8766`），容器間正常通訊不變，零效能損耗。
- **驗收條件**：
  - `public-nginx` 可正常轉發 `/api/v1/` 至 `public-api`。
  - 於 `public-api` 容器內測試對外連線（如連線外部 IP）立即失敗（Network unreachable）。
  - `cloudflared` 無法解析或連線 `public-api`、`private-api`、`private-nginx`。

---

### SEC-04（P1）：公開 Nginx IPv6 `/64` 子網歸戶限流與連線逾時

- **現況與位置**：`deploy/nginx-public.conf.template` 第 1 行與第 18–22 行：
  - `limit_req_zone $binary_remote_addr zone=search:1m rate=${SEARCH_RATE};`
  - 未設定連線／上游逾時與並發連線限制。
- **真實風險**：
  - 一般電信商與雲端主機配發給單一用戶的 IPv6 皆為 `/64` 子網（含 $2^{64}$ 個可用位址）。若以完整 128-bit `$binary_remote_addr` 限流，自動化腳本在同一個 `/64` 內隨機切換後 64 bits 即可完全繞過 `limit_req`，並快速塞滿 `1m` 狀態空間（約僅能容納 8,000 筆 IPv6 紀錄），造成正常訪客被誤判限流。
- **規格變更**：
  1. 在 `deploy/nginx-public.conf.template` 以 `map $remote_addr $limit_key` 將 IPv6 位址正規化至前 4 段（`/64` 前綴），IPv4 維持完整位址，作為 `limit_req_zone` 與 `limit_conn_zone` 的鍵值。
  2. 將 `zone=search:1m` 調升為 `zone=search:8m`（約可容納超過 6 萬～12 萬組狀態，`public-nginx` 現有 `mem_limit: 64m` 足夠容納）。
  3. 補齊合理短逾時與單鍵並發保護（例如 `client_header_timeout 10s; client_body_timeout 10s; send_timeout 15s; proxy_connect_timeout 5s; proxy_read_timeout 15s;` 與單鍵 `limit_conn` 上限如 `20`）。
  4. `set_real_ip_from` 補上 `::1` 與 `fc00::/7`，確保未來若環境啟用 Docker IPv6 仍能正確讀取 `CF-Connecting-IP`。
- **影響評估**：一般家庭或手機網路同一個 `/64` 共用限流桶（與 IPv4 家庭固網 NAT 共用一個公網 IP 行為完全對等），搭配 SEC-05 減少請求數後不影響正常瀏覽。
- **驗收條件**：`nginx -t` 通過；同一 IPv6 `/64` 網段內不同尾碼位址共用限流計數；既有容器整合測試通過。

---

### SEC-05（P1）：首頁「常用圖＋隨機骰圖」回源請求收斂與後端驗證快取

- **現況與位置**：
  - 前端：`src/anime_memes/public_ui/gallery.js`（第 115–139、275、300 行）與新增的骰子按鈕 `gallery-init.js`（第 24–42 行）。
  - 後端：`src/anime_memes/public_web.py`（第 24–34、84–91 行）與 `src/anime_memes/search.py`（第 25 行）。
- **真實風險（實測算術）**：
  - `search(true)` 在呼叫 `loadPinned()` 前會先把 `version` 清成空字串，導致每次載入首頁都會額外發出最多 **5 筆不帶 `version` 的 `/api/v1/entries/<id>`**（全數回傳 `no-store`，100% 穿透 Cloudflare 快取打回 VPS）。
  - 當訪客存有 5 張常用／收藏圖時：首次開站共發 **7 個 API 請求**（消耗 10 個 burst 額度中的 7 個）；若訪客在 1～2 秒內按下搜尋框旁新加入的「骰一張（`#rollMemes`）」按鈕，又會立刻再發 **6 個請求**（1 筆新隨機列表 + 重新抓 5 筆 `loadPinned`）——合計 **13 個請求**，直接觸發 Nginx `429 查詢過於頻繁`。
  - 後端每次請求（即使版本未變）都會對 SQLite 重跑 `validate_schema()` 共 6 條查詢；且非法格式 `entry_id`（非 32 字元 hex）仍會開啟資料庫查詢。
- **規格變更**：
  1. **前端 `loadPinned()` 合併與快取（`public_ui/gallery.js`）**：
     - 公開模式下，改為使用已支援的批量查詢 `GET /api/v1/entries?ids=<id1,id2,...>&version=<version>` 一次取回並依原常用順序排列（若已知 `version` 則帶上以命中 Cloudflare 快取；首次未知 `version` 時等主列表回應拿到 `data.version` 後，或與主列表合併／帶版本快取）。
     - 在同一個 `version` 期間，若常用／收藏清單未變動（例如連續點擊「骰一張」重抽隨機圖），直接沿用記憶體中已載入的 `pinnedItems`，**0 次額外請求**。點擊一次骰子從 6 個請求降為 **1 個請求**。
  2. **後端 `validate_schema` 快取與格式早退（`public_web.py`）**：
     - 在公開服務程序內以 `(version, stat.st_ino, stat.st_mtime_ns, stat.st_size)` 快取已通過 `validate_schema()` 的版本（最多保留少數幾筆），同一版本後續請求只開啟唯讀連線與 `BEGIN`，省去每次 6 條 schema PRAGMA 查詢。
     - `before_request` 與 `/api/v1/entries/<entry_id>` 先檢查路徑是否符合 `/api/v1/entries/[0-9a-f]{32}`，不符合者直接回 `404`，不開啟 SQLite。
- **影響評估**：畫面呈現與排序邏輯 100% 維持現狀，首頁與骰子連點體驗大幅改善，VPS 回源 API 請求量減少逾 70%。
- **驗收條件**：
  - 擁有 5 張常用圖的狀態下開啟首頁並連續點擊「骰一張」5 次，不會觸發 `429`，且點擊骰子時不再重發單筆 `/api/v1/entries/<id>`。
  - Node 前端狀態測試（`tests/test_gallery_state.cjs`）與 Python 後端契約測試全數通過。

---

### SEC-06（P2 選配）：公開 Nginx 與公開 API 細節收斂

- **變更內容**（低風險防禦深度，可順手處理）：
  1. `deploy/nginx-public.conf.template` 的 `location /assets/` 補上 `try_files $uri =404;`，並避免 `/assets/` 重複供應 `index.html`（或將靜態資源與 `index.html` 分開目錄）。
  2. `deploy/nginx-security-headers.conf`：若公開站確定只使用自有 R2 網域作為外部原圖來源，可評估將 `img-src` 與 `connect-src` 的萬用 `https:` 收緊（若仍保留手動填寫其他外部圖床網址之彈性，則維持現狀 `https:` 即可，屬功能取捨）。
  3. `src/anime_memes/public_web.py` 補上 `@app.errorhandler(500)` 回傳統一 JSON 格式 `{"error": {"code": "internal_error", "message": "..."}}`（與管理端 `web.py` 一致）。

---

## 4. 第二類：Cloudflare 邊緣、R2 CDN 與 VPS 維運評估項目

本章節不涉及應用程式邏輯改動，供維運人員對照現有 Cloudflare 與 VPS 設定進行核對與取捨。

### OPS-01（P0）：`meme.jwander.net` 邊緣防護核對清單

由於 Nginx 刻意不重複發送 HSTS，且第一線流量過濾位於 Cloudflare，公開前請核對：
1. **SSL/TLS**：已開啟 `Always Use HTTPS`，`Minimum TLS Version` 設為 `TLS 1.2`，並依 `docs/vps-deployment.md` 3.4 節啟用 HSTS。
2. **Cache Rule**：條件為 `Hostname equals meme.jwander.net` AND `URI Path starts with /api/v1/` AND `Query String contains version=`，遵循來源 `Cache-Control`（切勿快取未帶 `version=` 的請求）。
3. **WAF 託管規則與邊緣限流**：確認 Free Managed Ruleset 啟用；邊緣限流規則條件為 `/api/v1/`（完成 SEC-01 封鎖 `/health/` 後，動態路徑即只剩 `/api/v1/`）。

---

### OPS-02（P1）：R2 公開網域（`cdn.jwander.net`）防快取穿透與計費保護

- **背景與真實風險**：
  - R2 免收出站流量費（\$0 egress），但每次未命中 Cloudflare 邊緣快取而回源讀取 R2 物件會計入 **Class B Operations**（免費額度每月 1,000 萬次，全帳號共用）。
  - 若外部爬蟲或惡意腳本對有效圖片網址附加隨機參數（例如 `https://cdn.jwander.net/memes/<sha256>.webp?t=1,2,3...`），在預設「Standard」快取鍵規則下會因 Query String 不同而每次穿透快取讀取 R2。
- **建議 Cloudflare 設定（若先前已針對 CDN 設定過，核對即可）**：
  1. **關閉 Bucket 的 `*.r2.dev` 公開測試子網域**：確保所有讀取一律經由受控的 `cdn.jwander.net`，無法繞過自訂網域的快取與 WAF 規則。
  2. **Cache Rule（針對 `cdn.jwander.net`）**：
     - 條件：`Hostname equals cdn.jwander.net`
     - 設定 **Cache Key → Query String：Ignore query string**（所有 `?xxx` 皆視為同一快取物件，直接命中邊緣快取，不回源 R2）。
  3. **選配 WAF Custom Rule（路徑白名單）**：
     - 若 `cdn.jwander.net` 專供本專案使用（或固定前綴），可設定非允許路徑前綴（例如非 `^/memes/[0-9a-f]{64}\.webp$`，視該網域是否與其他專案共用而定）直接在邊緣 Block／404，避免隨機掃描不存在物件打進 R2。

---

### OPS-03（P2 選配）：VPS 部署用 SSH 金鑰權限收斂

- **背景與真實風險評估（不誇大）**：
  - 本機管理端只綁 `127.0.0.1`，公網訪客**無法**接觸到本機管理 API 或這把金鑰。
  - 此項僅針對「**共機隔離（Blast Radius）**」：目前本機 `data/deploy-ssh/id_ed25519` 登入的 VPS 帳號屬於 `docker` 群組（在 Linux 上等同無密碼 `root`）。若該台 VPS 同時跑有其他重要服務，萬一日後個人電腦備份外流或本機環境受損導致私鑰外洩，持有金鑰者可控制整台 VPS，而非僅限迷因資料庫目錄。
  - 若該 VPS 僅跑低敏感公開服務、或維運評估單人本機私鑰保管風險可控，**維持現狀是可接受的**；若希望與 VPS 上其他重要服務嚴格隔離，可採下列方案。
- **受限金鑰方案（供維運選用）**：
  1. 在 VPS 建立由 `root:root` 擁有（`0755`）的固定派發腳本 `/usr/local/sbin/memedatabase-deploy-wrapper`，僅允許執行白名單動作（`media-inventory`、`check-version`、`install-bundle`、`activate`、`prune`、以及對應 `/tmp/memedatabase-*.tar.gz` 的受限寫入與清理），拒絕任何其他參數或互動 shell。
  2. 在 `/etc/sudoers.d/memedatabase-deploy` 僅授權 `deploy` 帳號免密碼執行該單一腳本，並將 **`deploy` 帳號移出 `docker` 群組**。
  3. 在 `~deploy/.ssh/authorized_keys` 對應的發布公鑰前加上 `restrict,command="/usr/local/sbin/memedatabase-deploy-wrapper"`，並同步調整 `vps_publication.py` 與 `push_release.ps1` 的遠端呼叫參數（或於腳本解析 `SSH_ORIGINAL_COMMAND` 白名單）。

---

### OPS-04（P2 維運提醒）：公開投稿信箱（`meme@jwander.net`）安全注意事項

- 最新版公開頁提供了「準備投稿郵件」功能，公開顯示 `meme@jwander.net` 並引導訪客手動附加圖片寄信。
- 由於網站後端**不收信、也不自動解析郵件附件**，對 VPS 主機與公開網站本身**零安全風險**。
- 日常維運與審稿僅需注意兩點：
  1. 確認 `jwander.net` 已設定正確的 **SPF、DKIM 與 DMARC** DNS 紀錄，防止他人偽造 `@jwander.net` 名義寄出釣魚信。
  2. 收到外部投稿信件時，視為不可信外部檔案：不開啟壓縮檔、捷徑檔（`.lnk`）、執行檔、`.html` 或 `.svg`；僅將標準圖片檔（`.jpg`、`.png`、`.webp`、`.gif`）交由本專案匯入流程（經 Pillow 解碼與轉檔清洗）處理。

---

### OPS-05（標準維運程序）：誤發或爭議圖片的緊急下架（Takedown）閉環

當需要將某張已公開圖片「徹底且立即」從公開網域移除時，標準步驟如下（配合 SEC-02 完成後）：
1. **本機取消公開並匯出新版**：於管理頁將該圖取消公開 → 檢查並匯出新版本。
2. **推送至 VPS 並清理 VPS 舊版與縮圖**：推送並啟用新版，同時執行 `prune --keep-recent 0`（清除 VPS 上的舊版快照與不再引用的 `/media/<sha256>.webp` 縮圖；API 端因 SEC-02 已立即拒絕舊版查詢）。
3. **清理 R2 物件**：於本機管理頁「發布管理 → 徹底清除」執行清除（刪除 R2 上不再被引用的 720P `.webp` 物件，並複製畫面列出的 R2 網址）。
4. **清除 Cloudflare 邊緣快取**：至 Cloudflare 後台「Caching → Configuration → Purge Cache → Custom Purge（依網址）」，貼上該圖的 R2 網址（`https://cdn.jwander.net/memes/<sha256>.webp`）與公開縮圖網址（`https://meme.jwander.net/media/<sha256>.webp`）立即失效全球快取。

---

## 5. 第三類：公開程式碼庫衛生與本機管理端補強

### HYG-01（P0）：`.gitignore` 精準排除本機設定檔

- **現況**：
  - `.claude/skills/meme-tags/SKILL.md` 為專案共用的標籤維護技能檔（已納入 Git 追蹤且無敏感資訊，**必須保留追蹤**）。
  - `.claude/launch.json` 處於未追蹤狀態（`??`），內含本機 Windows 使用者名稱與暫存資料庫路徑，目前未被 `.gitignore` 排除。
  - `.codex/` 為本機工具資料夾，已列入 `.dockerignore` 但未列入 `.gitignore`。
- **規格變更**：
  - 於 `.gitignore` 新增 `.claude/launch.json` 與 `.codex/`（**不忽略整個 `.claude/`**，確保 `.claude/skills/` 正常受控）。
- **驗收條件**：`git status` 不再出現 `?? .claude/launch.json`；`git check-ignore .claude/skills/meme-tags/SKILL.md` 為空（維持追蹤）。

---

### HYG-02（P1）：範例檔與歷史規格文件去識別化

- **現況**（已確認不含個人使用者帳號，僅含專案目錄名與範例 Discord 附件編號）：
  1. `examples/tagging_jobs.jsonl` 第 1 行：`"image_path":"D:/MainProject/AnimeMemes/data/attachments/example.png"`
  2. `examples/llm_suggested_tags.jsonl` 第 1 行、`examples/manual_tags.csv` 第 2 行：`meme_1393137272077156373`
  3. `docs/archive/vision-tagging-stage-spec.md` 第 311–329 行：`D:/MainProject/AnimeMemes/data/attachments/1393132585340633221.jpg` 與 `meme_1393132585340633221`
- **規格變更**：
  - 將上述 `image_path` 改為相對或通用範例路徑（如 `data/attachments/example.png` 或 `/path/to/repo/data/attachments/example.jpg`）。
  - 將真實 19 位數 Discord 附件編號替換為明顯的假範例編號（如 `meme_1000000000000000001`）。
- **驗收條件**：全倉搜尋（排除測試中的固定負面測試字串）不再出現本機磁碟路徑與真實附件編號；既有單元測試通過。

---

### HYG-03（P2 選配）：本機管理端與離線 CLI 防禦深度補強

此組項目**完全不在公開映像與公開網域內**，僅在管理者本機匯入外部檔案或不使用 Docker 直接執行 `web` 指令時提供額外保護：
1. **管理詳情外部連結協定檢查（`src/anime_memes/web_static/app-detail.js`、`web.py`）**：
   - 當本機原圖不存在時，`attachment_url` 回退使用 `discord_url`；前端產生 `<a href="...">` 前先確認網址以 `/media/`、`https://` 或 `http://` 開頭，否則不渲染為可點擊連結。
   - 於 `web.py` 的 `after_request` 補上基本安全標頭（`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: strict-origin-when-cross-origin`），讓不經 Nginx 的原生本機 `web` 模式（port 3638）也具備一致保護。
2. **本機 CLI 下載器與路徑邊界檢查（`web_import.py`、`attachments.py`、`media.py`）**：
   - `attachments.attachment_path()` 驗證 `attachment_id` 不含路徑分隔符（`/`、`\`、`..`）。
   - `attachments.download_attachment()` 加入與手動上傳一致的大小上限（如 50 MB），避免異常連結無限寫入磁碟。
   - `web_import.fetch_image()` 拒絕本機與私網位址（含重導向目標檢查）。
   - `media.media_path()` 比照 `safe_delete_media_file()`，在回傳檔案前確認解析後路徑位於允許的媒體根目錄內且非符號連結。
