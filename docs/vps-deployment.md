# VPS 部署手冊

本手冊說明如何把公開搜尋站（`https://meme.jwander.net`）部署到 VPS，並透過 Cloudflare Tunnel 對外提供。發布包的格式、`runtime` 指令與公開 API 契約見 [雙版本後端與發布操作](backend-refactor.md)。

文中指令區塊：`bash` 在 VPS 上執行，`powershell` 在本機執行。`<tunnel token>`、`myvps` 等為佔位符，請換成自己的值，不要把真實秘密寫進文件或提交到 Git。

## 1. 架構概覽

本次首次上線採用專案獨立的 Docker Tunnel。VPS 先啟動公開 API 與 Nginx，再將 Tunnel token 寫入 VPS 的 `.env` 並啟動 `cloudflared`。公開路由可從既有 Tunnel 的「路由 → 新增已發佈的應用程式」設定。VPS 的程式更新與本機管理頁的館藏發布各有自己的流程。

```text
訪客 ──HTTPS──> Cloudflare 邊緣（TLS、快取）
                      │
                      │  Cloudflare Tunnel（由 VPS 主動連出）
                      ▼
        VPS：cloudflared ──> public-nginx:8080 ──> public-api:8765
                                   │                    │
                                   └─ /srv/media (唯讀)  └─ /releases (唯讀)
                                          ▲
                              ${RELEASE_ROOT}（例如 /srv/memedatabase/releases）
                                          ▲
本機管理端（3636，永不上 VPS）─ push_release.ps1 ──scp / ssh──┘
```

- VPS 不開任何連入 port，防火牆只開 SSH。cloudflared 主動連出到 Cloudflare；public-nginx 對主機只綁 `127.0.0.1:3637`，僅供 VPS 本機檢查。
- 私人管理端（private profile、3636）與 MCP 只在使用者本機執行，永遠不接上 Tunnel，也不部署到 VPS。
- VPS 只跑 `public` profile（public-api、public-nginx）與 `tunnel` profile（cloudflared）。內容只有管理端勾選發布的梗圖，任何人都可瀏覽。

## 2. 事前準備

- 網域 `jwander.net` 的 DNS 由 Cloudflare 代管。
- 一台 VPS：Linux，已安裝 Docker Engine 與 compose plugin（`docker compose version` 可執行）。
- 本機 `~/.ssh/config` 已有 VPS 別名（以下以 `myvps` 表示），可用金鑰登入。
- 本機已能匯出發布版本（管理頁與 `.venv`，見 [雙版本後端與發布操作](backend-refactor.md)）。

## 3. Cloudflare 設定

以下在 Cloudflare 控制台操作。介面名稱可能隨時間調整，以實際控制台為準。

### 3.1 建立 Tunnel

1. Zero Trust → Networks → Tunnels → Create a tunnel，類型選 Cloudflared。
2. 命名（例如 `memedatabase`），在安裝步驟頁面複製 token（只需要 token，不必照畫面在 VPS 手動安裝 cloudflared）。token 是秘密，稍後只放進 VPS 的 `.env`。

### 3.2 Public Hostname

在該 Tunnel 的 Public Hostname 新增：

| 欄位 | 值 |
|---|---|
| Subdomain / Domain | `meme` / `jwander.net`（即 `meme.jwander.net`） |
| Path | 留空 |
| Service Type | `HTTP` |
| URL | `public-nginx:8080` |

`public-nginx` 是 compose 內的服務名稱，cloudflared 與它在同一個 compose 網路，可直接以名稱連線。儲存時 Cloudflare 會自動建立指向此 Tunnel 的 DNS CNAME。

Tunnel 請維持指向 `8080`。VPS 主機的 `127.0.0.1:3637` 轉接至容器的 `8081`，可使用 `/health/live` 與 `/health/ready`。公開入口的健康檢查路徑會回 404。

### 3.3 Cache Rule

Cloudflare 預設會依副檔名快取靜態檔（webp、js、css），但 JSON API 預設不快取。公開 API 帶 `version` 參數且回 200 的 `/api/v1/*` 回應是 `Cache-Control: public, max-age=31536000, immutable`，需要一條規則讓邊緣遵循它。

Caching → Cache Rules → Create rule，條件全部成立（AND）：

- Hostname equals `meme.jwander.net`
- URI Path starts with `/api/v1/`
- Query String contains `version=`

動作：

- Cache eligibility：Eligible for cache
- Edge TTL：Use cache-control header if present

沒有 `version` 參數的請求不可快取（來源本來就回 `no-store`），所以條件一定要包含查詢字串這一項。`/` 與未加雜湊的 `/assets/` 檔案來源是 `no-cache`；首頁引用的 JS、CSS、SVG 在 Nginx 映像建置時改為帶內容雜湊的檔名（`deploy/hash-assets.sh`），來源回 `immutable` 長期快取。兩者都維持預設即可。

換版當下，來源 API 只接受目前啟用的版本。緊急下架時，啟用新版後可用 `-Activate -Prune -KeepRecent 0` 的發布流程清除舊版及其獨佔縮圖。已快取的舊版 API 與縮圖須清除 Cloudflare 快取，涵蓋舊版查詢字串與所有受影響的 URL。若原圖存於 R2，也須刪除對應物件並清除 CDN 快取。訪客瀏覽器已下載的內容無法遠端撤回。

### 3.4 SSL/TLS 與 HTTPS

- SSL/TLS 加密模式：Tunnel 的流量由 cloudflared 加密連到 Cloudflare，來源端不需要憑證，也不需要在 Nginx 設定 TLS。
- SSL/TLS → Edge Certificates：開啟 Always Use HTTPS。
- HSTS 在 Cloudflare 開啟，Nginx 不送 HSTS。建議先用短 max-age（例如 1 天或 1 週）測試，確認網站與子網域都正常後再拉長。除非確定 `jwander.net` 所有子網域都走 HTTPS，否則不要勾 includeSubDomains。
- SSL/TLS → Edge Certificates：Minimum TLS Version 設為 TLS 1.2。

### 3.5 安全防護（WAF、限流、Bot）

以下都是免費方案可用的設定。Cloudflare 擋在最前面，流量進到 VPS 之前先過濾；VPS 上的 Nginx 限流是第二道防線，兩者並用。

| 項目 | 位置 | 建議設定 |
|---|---|---|
| DDoS 防護 | 自動 | 預設啟用，不需設定 |
| WAF 託管規則 | Security → WAF → Managed rules | 確認免費託管規則集（Free Managed Ruleset）為啟用 |
| 邊緣限流 | Security → WAF → Rate limiting rules | 新增一條，見下方 |
| Bot Fight Mode | Security → Bots | 可開啟；開啟後實測搜尋、載入更多、詳情與圖片都正常 |
| Security Level／Browser Integrity Check | Security → Settings | 維持預設 |
| Under Attack Mode | Overview | 平時關閉，只在遭受攻擊時臨時開啟（訪客會先看到驗證頁） |

邊緣限流規則（免費方案只能建一條，計數週期與封鎖時間固定為 10 秒）：

- 條件：Hostname equals `meme.jwander.net` 且 URI Path starts with `/api/v1/`
- 依據：IP
- 門檻：每 10 秒 50 個請求
- 動作：Block，持續 10 秒

門檻刻意比 Nginx 寬（Nginx 為每秒 2 次、可瞬間多 10 次，約等於 10 秒 30 次）：邊緣只擋明顯的大量請求，正常使用時的細部限流交給 Nginx，避免一般訪客快速翻頁時先被 Cloudflare 擋下。帶 `version` 參數的請求若命中邊緣快取，不會到達 VPS，但仍會被這條規則計數。

本站是公開網站，不需要 Cloudflare Access（登入保護）；只有想限定特定人瀏覽時才需要。

## 4. VPS 首次部署

### 4.1 安裝 Docker

依 [Docker 官方安裝文件](https://docs.docker.com/engine/install/) 選擇自己發行版的步驟安裝 Docker Engine 與 compose plugin，完成後確認：

```bash
docker --version
docker compose version
```

### 4.2 防火牆、SSH 與系統更新

#### 防火牆只開 SSH

以 ufw 為例（請先確認 SSH port，避免把自己鎖在外面）：

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status verbose
```

不需要開 80、443 或 3637。注意 Docker 發佈的 port 可能繞過 ufw 規則；本專案只綁 `127.0.0.1`，不受影響，請不要改成綁 `0.0.0.0`。

#### SSH 只允許金鑰登入

先確認已能用金鑰登入，並**保留一個已登入的 SSH 視窗**，改完確認新連線可登入後再關閉，避免把自己鎖在外面。

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl reload ssh   # 部分發行版的服務名稱是 sshd
```

`sshd -t` 檢查設定語法，失敗時不要重新載入。若主設定檔沒有 `Include /etc/ssh/sshd_config.d/*.conf`，改為直接編輯 `/etc/ssh/sshd_config`。

#### 自動安全更新

Debian／Ubuntu：

```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

這只更新主機套件，不會更新容器映像；映像更新見 6.2。

#### 選用：fail2ban

已改為金鑰登入且防火牆只開 SSH 時，暴力破解幾乎無法成功，fail2ban 主要用來減少日誌雜訊，可視需要安裝。

### 4.3 部署使用者

現行部署由 VPS 的 systemd 以 root 執行 Compose，本機館藏發布走本章後述的受限 SSH。例行館藏發布使用本機管理頁。若沿用 `push_release.ps1` 的傳統 SSH／SCP 模式，該模式的 SSH 使用者必須能直接執行 Docker。

```bash
sudo adduser deploy            # 已有使用者可略過
sudo usermod -aG docker deploy # 重新登入後生效
```

提醒：docker 群組成員等同 root 權限，只給信任的帳號，並使用 SSH 金鑰、停用密碼登入。

### 4.4 部署目錄與設定檔

VPS 不放原始碼，也不在 VPS 建置映像；公開映像由 GitHub Actions 建好推到 GHCR（見 6.2）。

- `/opt/memedatabase`：部署目錄，只放 `.env` 與部署狀態（`.ghcr-deployed.env`），**不是 git checkout**，不要在這裡 `git pull` 或從這裡安裝腳本。
- `/etc/memedatabase/`：Compose 設定（`compose.yaml`、`compose.ghcr.yaml`），root 擁有、模式 `0644`。
- `/usr/local/sbin/`：程式部署與館藏發布兩支受限指令（見 6.2、6.4）。

設定檔一律從 GitHub raw 取得 `main` 的版本：

```bash
base=https://raw.githubusercontent.com/Jwander0820/MemeDatabase/main
sudo mkdir -p /opt/memedatabase /etc/memedatabase
sudo curl -fsSL "$base/compose.yaml" -o /etc/memedatabase/compose.yaml
sudo curl -fsSL "$base/deploy/compose.ghcr.yaml" -o /etc/memedatabase/compose.ghcr.yaml
sudo chmod 0644 /etc/memedatabase/*.yaml
```

Compose 拓樸（`compose.yaml`／`deploy/compose.ghcr.yaml`）有變更時，同樣以上述指令重新下載；一般程式更新不需要。

### 4.5 建立發布目錄

容器以 UID 10001 執行，發布目錄必須事先建好並由 10001 擁有（`install-bundle` 要寫入）：

```bash
sudo mkdir -p /srv/memedatabase/releases/{versions,media,reports}
sudo chown -R 10001:10001 /srv/memedatabase/releases
```

compose 對發布目錄的掛載設了 `create_host_path: false`：在 Linux 的 Docker Engine 上，目錄不存在時 `up` 會直接失敗，不會讓 Docker 自動建立 root 擁有的目錄。這個設定在 Windows／macOS 的 Docker Desktop 上不一定生效（實測 Windows 會照樣建立目錄），所以**一律先依上面的指令建立目錄並 chown**，不要依賴 Docker 自動建立。

### 4.6 建立 .env

在 `/opt/memedatabase/.env`（部署以 root 執行，檔案由 root 擁有）：

```bash
sudo tee /opt/memedatabase/.env >/dev/null <<'EOF'
CLOUDFLARE_TUNNEL_TOKEN=<tunnel token>
RELEASE_ROOT=/srv/memedatabase/releases
COMPOSE_PROFILES=public,tunnel
# 以下可選
# SEARCH_RATE=2r/s
# SEARCH_BURST=10
# PUBLIC_PORT=3637
# CLOUDFLARED_VERSION=版本@sha256:已查證的映像索引digest（預設使用 compose.yaml 的固定值）
EOF
sudo chmod 600 /opt/memedatabase/.env
```

不需要任何 R2、Discord 或 MCP 變數。`.env` 含秘密，不可提交。既有 Tunnel 的 token 可從 Cloudflare「概觀 → 新增副本 → Docker」複製，取安裝指令中 `--token` 後面的值。

### 4.7 啟動

公開 API 與 Nginx 由 GitHub Actions 第一次部署時拉取 GHCR 映像並啟動（依 6.2 完成設定後，從 Actions 手動執行一次部署）。Compose 專案名稱固定為 `memedatabase`，容器為 `memedatabase-public-api-1` 等。

程式部署只更新 public-api 與 public-nginx，不動 cloudflared。公開容器正常後，以同一組 Compose 參數啟動 Tunnel：

```bash
sudo docker compose --project-directory /opt/memedatabase --env-file /opt/memedatabase/.env \
  -f /etc/memedatabase/compose.yaml --profile tunnel up -d --no-deps cloudflared
```

### 4.8 驗證

```bash
curl -fsS http://127.0.0.1:3637/health/live
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3637/health/ready
```

`/health/live` 應成功。此時 `/health/ready` 回 503 是正常的，代表還沒有啟用任何版本（服務健康檢查用 live，所以沒有版本也能啟動）。

## 5. 第一次發布

1. 在本機管理頁選圖、檢查並匯出版本（記下版本號 `VERSION`）。
2. 推送並啟用：

```powershell
.\scripts\push_release.ps1 -Version VERSION -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases -Activate
```

建議先加 `-DryRun` 看一遍將執行的命令。

3. 在 VPS 驗證：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3637/health/ready   # 應為 200
```

4. 瀏覽 <https://meme.jwander.net>，確認搜尋與縮圖正常。

## 6. 日常操作

### 從本機發布管理操作

本機管理頁的「發布管理」已提供「發布到 VPS 正式站」。先在本機檢查並匯出版本，再選擇已匯出的版本、按「推送到 VPS」。預設只安裝；勾選「安裝驗證成功後，立即啟用正式站新版」才會切換線上版本。「本機啟用」只影響本機測試站，不會更新 VPS。這個功能只發布館藏，不更新 Docker 程式映像。

連線目標（SSH 主機別名或 `user@host`、遠端發布目錄）可在此區的「VPS 連線設定」儲存；設定與最近一次工作結果放在私人資料目錄 `vps-publication/`，不隨公開發布包上傳。背景工作會顯示比對、打包、傳輸、安裝等階段，以及需傳送／沿用的縮圖數與包大小；目前沒有逐位元組的傳輸百分比。關閉對話框或重新整理後，可重新開啟查看結果。同時只允許一個 UI 發布工作；不要與 PowerShell 上傳腳本同時執行。

使用本機 Docker 私人服務時，請先在 `data/deploy-ssh/`（或 `${PRIVATE_DATA_ROOT}/deploy-ssh/`）放入專用的 `id_ed25519` 與已核對的 `known_hosts`；可另放 `config` 設定主機別名，例如：

```sshconfig
Host myvps
    HostName 你的VPS主機名稱或IP
    User deploy
```

私鑰需能以非互動方式使用，且公鑰已安裝到 VPS 部署帳號。主機指紋應先從可信來源確認，不要略過驗證；網頁不接收密碼／私鑰，也不自動接受陌生主機指紋。此專用目錄只掛載到私人容器 `/deploy-ssh`；執行時複製到私有暫存並設為 `0600`，避免 Windows 掛載權限造成 OpenSSH 拒絕。不得提交這些檔案。若是原生 Python 管理服務，則沿用該執行帳號的 SSH config／agent；可用 `MEMES_VPS_SSH_DIR` 指定相同的專用目錄。

VPS 須先部署支援 `media-inventory` 的公開映像（預設 `memedatabase-public-api`；需要時由私人服務的 `MEMES_VPS_IMAGE` 指定其他名稱），並完成發布目錄權限設定。本機私人 Docker 已安裝 OpenSSH client，公開映像不安裝此工具。

若使用專用受限金鑰，在本機 Compose 的 `.env` 加入 `MEMES_VPS_RESTRICTED_SSH=1`，並重建私人 API。VPS 的 root 須將 [發布指令](../deploy/vps-publication.sh) 安裝為 `/usr/local/sbin/memedatabase-publication`，由發布帳號的專用 SSH 公鑰以 `restrict` 和 forced command 綁定到下列指令。服務使用 `/srv/memedatabase/releases` 和執行中公開 API 的映像 ID，發布包透過 SSH 標準輸入上傳，由 VPS 暫存並清除。此金鑰具備啟用公開館藏版本的權限。

```text
restrict,command="/usr/bin/sudo -n -- /usr/local/sbin/memedatabase-publication \"$SSH_ORIGINAL_COMMAND\"" ssh-ed25519 <專用公開金鑰>
```

頁面顯示「完成」代表遠端安裝／啟用指令成功，仍應實際開啟公開網址檢查。每次推送會先確認此版本是否已安裝：內容完全相同時直接沿用，需要時可勾選啟用，不再重傳；同名但內容不同則拒絕覆寫。連線逾時或本機服務重啟可能無法確認遠端是否完成；畫面會標示失敗或結果待確認，不自動重試。請先確認遠端工作已結束，再手動重試。本機服務重啟後不會自動接續舊工作。目前未提供遠端取消或系統通知；關閉頁面不會取消發布。

### 6.1 發布新版本

本機匯出新版本後：

上傳腳本預設採縮圖增量傳輸：先取得 VPS 已有且雜湊正確的縮圖清單，只上傳缺少的縮圖；DB 與完整 manifest 每次都更新。縮圖在本機產生，VPS 不下載 R2 大圖重新產圖。需先更新 VPS 公開映像以支援 `media-inventory`；舊映像可加 `-Full` 使用完整傳輸，清單查詢失敗時不會繼續上傳。

```powershell
.\scripts\push_release.ps1 -Version NEW_VERSION -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases -Activate
```

不加 `-Activate` 只安裝並驗證，不切換目前版本；之後可在 VPS 以 `activate` 切換（見 6.3）。已存在的版本不會被覆寫。

改文字／標籤只更新資料快照；新增或換圖只傳新雜湊圖片。刪除資料／取消公開在啟用新 DB 後生效，舊縮圖先保留供回滾，後續依 `-Prune` 清理未被保留版本引用的檔案。增量包依賴目標 VPS 的既有縮圖；離線完整備份請使用 `-Full`。此清理不會刪除 R2 或清除 CDN 快取。

從管理頁發布並啟用時，VPS 會在啟用成功後自動清理舊版本（見 6.4）。用上傳腳本時，加上 `-Prune` 會在安裝後清理，保留最新三版與目前啟用版本，避免 VPS 上累積太多版本：

```powershell
.\scripts\push_release.ps1 -Version NEW_VERSION -SshHost myvps -RemoteReleaseRoot /srv/memedatabase/releases -Activate -Prune
```

清理不需停止公開服務。

### 6.2 程式更新（GitHub Actions＋GHCR）

推送到 `main` 就會部署，不需要登入 VPS：

1. PR 階段由 [CI workflow](../.github/workflows/ci.yml) 跑 Python 單元測試、腳本語法、Compose 設定與前端測試（不建置、不部署）。
2. 合併到 `main` 後，[部署 workflow](../.github/workflows/deploy.yml) 再跑一次測試，建置兩個 `linux/amd64` 公開映像推到 GHCR。
3. 儲存庫變數 `VPS_GHCR_DEPLOY_ENABLED=true` 時，透過受限 SSH 把同一次提交的兩個映像 digest 交給 VPS；VPS 確認提交仍是 `main` 最新版、拉取並核對映像後才更新容器，健康檢查失敗會恢復上次成功的映像。
4. 結果記在 `/opt/memedatabase/.ghcr-deployed.env`（提交與映像 digest）。需要重跑時，從 Actions 對 `main` 手動執行部署 workflow。

**確認上線版本**：部署 workflow 會把提交 SHA 與建置時間（UTC）寫進兩個公開映像。

- 公開站頁尾最右邊的淡灰色 7 碼就是提交短碼，滑鼠停在上面會顯示建置時間；與 GitHub `main` 最新提交的短碼相同即代表已部署。也可以檢視原始碼找 `<meta name="build-version">`。
- `GET /api/v1/meta`（不帶 `version`）回傳 `build.revision`、`build.built_at`，反映執行中的 API 映像。帶 `version` 的回應會被長期快取，不含版號。
- 首頁 `/` 是 `no-cache`，重新整理就會拿到最新頁面；本機建置沒有版號，頁尾不顯示。

測試用的 Python 版本一律從 `Dockerfile` 的基底映像讀取（目前 3.15），本機與 CI 不另外維護版本號。

**首次設定**（已完成則略過）：

1. GitHub Settings → Environments 建立 `production`，部署分支限 `main`，設定 `VPS_HOST`、`VPS_PORT`、`VPS_USER`（`ubuntu`）、`VPS_KNOWN_HOSTS`、`VPS_SSH_KEY`（程式部署專用金鑰）。
2. 在 VPS 安裝程式部署指令（Compose 設定見 4.4）：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Jwander0820/MemeDatabase/main/deploy/vps-code-deploy.sh -o /tmp/memedatabase-ghcr-deploy
   sudo install -o root -g root -m 0755 /tmp/memedatabase-ghcr-deploy /usr/local/sbin/memedatabase-ghcr-deploy
   rm /tmp/memedatabase-ghcr-deploy
   ```

   程式部署公鑰的 `authorized_keys` 條目：

   ```text
   restrict,command="/usr/bin/sudo -n -- /usr/local/sbin/memedatabase-ghcr-deploy \"$SSH_ORIGINAL_COMMAND\"" ssh-ed25519 <程式部署專用公開金鑰>
   ```

3. 舊的 `memedatabase-deploy.timer`（VPS 輪詢並自行建置）已停用；部署指令偵測到它在執行會拒絕部署。
4. 首次建置後，到 GitHub Packages 將 `memedatabase-public-api`、`memedatabase-public-nginx` 設為 **Public**，讓 VPS 匿名拉取，再把 `VPS_GHCR_DEPLOY_ENABLED` 設為 `true`。

若更新包含公開資料結構（schema）版本升級，舊版本會因不相容而無法 ready，需在本機重新匯出並推送、啟用新版本。

**映像與依賴更新**：主機的自動更新不會更新容器內容。Python、Nginx 與預設 cloudflared 都固定「版本＋digest」，由 [Dependabot](../.github/dependabot.yml) 每月檢查基底映像、cloudflared 與 GitHub Actions 並開 PR；合併後照一般程式更新流程部署。Python 套件鎖檔不交給 Dependabot，以 `scripts/lock_dependencies.ps1 -Upgrade` 手動重產（見[建置鎖檔與映像更新](backend-refactor.md#建置鎖檔與映像更新)）。cloudflared 不隨程式部署更新，版本變更後需重新下載 `compose.yaml` 並以 4.7 的指令重啟。`.env` 的 `CLOUDFLARED_VERSION` 可覆寫預設，應填 `版本@sha256:已查證的digest`。被取代的舊映像可用 `sudo docker image prune -f` 清除。

### 6.3 回到舊版與其他 runtime 指令

在 VPS 以執行中公開 API 的映像執行 runtime 指令（`--root /releases` 對應 `-v` 掛載）。GHCR 部署的映像沒有 `memedatabase-public-api` 這個本機名稱，先取出映像 ID：

```bash
image=$(sudo docker inspect --type container --format '{{.Image}}' memedatabase-public-api-1)
sudo docker run --rm --user 10001:10001 -v /srv/memedatabase/releases:/releases "$image" \
  python -m src.anime_memes.runtime rollback --root /releases OLD_VERSION
```

同樣方式可執行 `validate VERSION` 與 `activate VERSION`，只要把 `rollback --root /releases OLD_VERSION` 換成 `validate --root /releases VERSION` 或 `activate --root /releases VERSION`。切換不需要重啟服務。

### 6.4 清理舊版本

`prune` 保留最新三個版本與目前啟用版本，其餘版本與不再被引用的縮圖才刪除。服務執行中即可清理：公開 API 每個請求只讀目前啟用版，指定其他版本一律回 410。

受限發布指令（`/usr/local/sbin/memedatabase-publication`）在 `activate` 或 `install-and-activate` 成功後會自動清理（`--keep-recent 3`）；清理失敗只記警告，不影響已完成的啟用。這支指令不會隨程式部署更新，改版後由 root 從 GitHub raw 重新安裝：

```bash
curl -fsSL https://raw.githubusercontent.com/Jwander0820/MemeDatabase/main/deploy/vps-publication.sh -o /tmp/memedatabase-publication
sudo install -o root -g root -m 0755 /tmp/memedatabase-publication /usr/local/sbin/memedatabase-publication
rm /tmp/memedatabase-publication
```

手動清理：

```bash
image=$(sudo docker inspect --type container --format '{{.Image}}' memedatabase-public-api-1)
sudo docker run --rm --user 10001:10001 -v /srv/memedatabase/releases:/releases "$image" \
  python -m src.anime_memes.runtime prune --root /releases
```

## 7. 檢查與疑難排解

VPS 沒有原始碼目錄，Compose 指令要帶部署目錄與設定檔；可先定義簡寫：

```bash
dc() { sudo docker compose --project-directory /opt/memedatabase --env-file /opt/memedatabase/.env \
  -f /etc/memedatabase/compose.yaml --profile public --profile tunnel "$@"; }
dc ps
dc logs cloudflared
dc logs public-nginx public-api
```

查看最近一小時到達公開站的關鍵字搜尋（含翻頁）：

```sh
dc logs --since 1h --timestamps public-nginx | grep 'search status='
```

每行代表一次請求，例如 `search status=200 seconds=0.024` 表示成功且耗時 24 毫秒。429 表示限流，5xx 表示伺服器錯誤。這份存取日誌省略搜尋詞、訪客 IP、健康檢查與一般瀏覽；瀏覽器或 CDN 直接回應的請求也不會出現。

- cloudflared 無法連線：看 `dc logs cloudflared`。token 空白或錯誤、VPS 無法連出，都會在這裡顯示。確認 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN` 正確且沒有多餘空白或引號。
- 網站 502／1033 等：先在 VPS 用 `curl http://127.0.0.1:3637/health/live` 確認 public-nginx 正常，再確認控制台 Public Hostname 的 Service 是 `HTTP` 與 `public-nginx:8080`。
- 429：搜尋限流依訪客 IP 計算。Nginx 只信任私有網段與 127.0.0.1 來源的 `CF-Connecting-IP` 作為真實 IP。若所有人都被一起限流，表示 Nginx 看到的是同一個來源 IP（例如沒有取到 `CF-Connecting-IP`），請檢查請求是否真的經過 Cloudflare／cloudflared，必要時暫時調高 `SEARCH_RATE`、`SEARCH_BURST` 並重建。
- 首次部署報目錄不存在（類似 `bind source path does not exist`）：`create_host_path: false` 的設計，代表 `RELEASE_ROOT` 或其下的 `media` 目錄還沒建立，或 `.env` 的路徑有誤。依 4.5 建立後再 `up`。
- `install-bundle` 權限錯誤：發布目錄需由 UID 10001 擁有（`chown -R 10001:10001`）。
- `/health/ready` 一直 503：尚未啟用任何版本，或版本與目前程式的 schema 不相容；用 `activate` 或重新匯出推送。
- 映像名稱：GHCR 部署後 VPS 上沒有 `memedatabase-public-api` 這個本機映像名稱。建議用管理頁搭配受限發布指令（自動使用執行中公開 API 的映像）；若仍用 `push_release.ps1` 的傳統模式，以 `-Image` 指定 `.ghcr-deployed.env` 記錄的 GHCR 映像。

## 8. 安全提醒

### 防護分層總覽

| 層級 | 已在程式／設定中完成 | 需自行設定 |
|---|---|---|
| Cloudflare 邊緣 | — | HTTPS、HSTS、最低 TLS 1.2（3.4）；WAF、邊緣限流、Bot（3.5）；快取規則（3.3） |
| 網路 | Tunnel 只連出，VPS IP 不公開；所有 port 只綁 127.0.0.1 | 防火牆只開 SSH（4.2） |
| VPS 主機 | — | SSH 只允許金鑰、自動安全更新（4.2）；合併 Dependabot 映像更新 PR（6.2） |
| Nginx | 依訪客真實 IP 限流（每秒 2 次、可瞬間多 10 次）；只開放必要路徑；請求上限 1 MB；CSP、X-Frame-Options、Referrer-Policy 等安全標頭；隱藏版本、不列目錄 | — |
| 公開 API | 只接受讀取；參數白名單；分頁、查詢長度（256 字／16 詞）、收藏數（200）上限；資料庫唯讀且只含已發布欄位 | — |
| 容器 | Python 服務以 UID 10001、Nginx 以 UID 101（nginx-unprivileged）執行；所有服務 `cap_drop: ALL`、禁止提權、根檔案系統唯讀（僅 tmpfs 與必要掛載可寫）；記憶體、CPU、程序數上限；日誌輪替；Python／Nginx 基底與預設 cloudflared 固定版本及 digest，三種 Python 部署使用完整版本與 SHA-256 鎖檔 | 合併 Dependabot PR 或重產鎖檔後部署，才能取得安全修補 |

建置鎖檔適用 Linux 與 Dockerfile 基底映像的 Python 版本，未包含本機 OCR／CUDA。固定 digest 與雜湊不等於已完成安全更新或主機負載驗收；更新與還原仍依維運流程執行。

### 其他提醒

- 管理端（3636）與 MCP 永不接上 Tunnel，也不要在 VPS 啟用 `private`、`mcp` profile。
- Tunnel token 外洩時，在 Cloudflare 控制台重設（refresh token），更新 VPS 的 `.env` 後重啟 cloudflared。
- VPS 只放公開資料（已發布的版本與縮圖）。管理庫與原圖只在本機。
- 待辦：管理庫與原圖的備份需在本機另外規劃，目前尚未建立異地備份。
