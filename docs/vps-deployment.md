# VPS 部署手冊

本手冊說明如何把公開搜尋站（`https://meme.jwander.net`）部署到 VPS，並透過 Cloudflare Tunnel 對外提供。發布包的格式、`runtime` 指令與公開 API 契約見 [雙版本後端與發布操作](backend-refactor.md)。

文中指令區塊：`bash` 在 VPS 上執行，`powershell` 在本機執行。`<tunnel token>`、`myvps` 等為佔位符，請換成自己的值，不要把真實秘密寫進文件或提交到 Git。

## 1. 架構概覽

```text
訪客 ──HTTPS──> Cloudflare 邊緣（TLS、快取）
                      │
                      │  Cloudflare Tunnel（由 VPS 主動連出）
                      ▼
        VPS：cloudflared ──> public-nginx:8080 ──> public-api:8765
                                   │                    │
                                   └─ /srv/media (唯讀)  └─ /releases (唯讀)
                                          ▲
                              ${RELEASE_ROOT}（例如 /srv/animememes/releases）
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
2. 命名（例如 `animememes`），在安裝步驟頁面複製 token（只需要 token，不必照畫面在 VPS 手動安裝 cloudflared）。token 是秘密，稍後只放進 VPS 的 `.env`。

### 3.2 Public Hostname

在該 Tunnel 的 Public Hostname 新增：

| 欄位 | 值 |
|---|---|
| Subdomain / Domain | `meme` / `jwander.net`（即 `meme.jwander.net`） |
| Path | 留空 |
| Service Type | `HTTP` |
| URL | `public-nginx:8080` |

`public-nginx` 是 compose 內的服務名稱，cloudflared 與它在同一個 compose 網路，可直接以名稱連線。儲存時 Cloudflare 會自動建立指向此 Tunnel 的 DNS CNAME。

### 3.3 Cache Rule

Cloudflare 預設會依副檔名快取靜態檔（webp、js、css），但 JSON API 預設不快取。公開 API 帶 `version` 參數且回 200 的 `/api/v1/*` 回應是 `Cache-Control: public, max-age=31536000, immutable`，需要一條規則讓邊緣遵循它。

Caching → Cache Rules → Create rule，條件全部成立（AND）：

- Hostname equals `meme.jwander.net`
- URI Path starts with `/api/v1/`
- Query String contains `version=`

動作：

- Cache eligibility：Eligible for cache
- Edge TTL：Use cache-control header if present

沒有 `version` 參數的請求不可快取（來源本來就回 `no-store`），所以條件一定要包含查詢字串這一項。`/` 與 `/assets/` 來源是 `no-cache`，維持預設即可。

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

這只更新主機套件，不會更新容器映像；映像的更新見 6.2。

#### 選用：fail2ban

已改為金鑰登入且防火牆只開 SSH 時，暴力破解幾乎無法成功，fail2ban 主要用來減少日誌雜訊，可視需要安裝。

### 4.3 部署使用者

使用既有使用者，或建立專用使用者，並加入 `docker` 群組。`push_release.ps1` 的 SSH 使用者必須能執行 docker。

```bash
sudo adduser deploy            # 已有使用者可略過
sudo usermod -aG docker deploy # 重新登入後生效
```

提醒：docker 群組成員等同 root 權限，只給信任的帳號，並使用 SSH 金鑰、停用密碼登入。

### 4.4 取得程式碼

最簡單的做法是在 VPS 上 clone 整個 repo（`data/` 已在 `.gitignore`，不會有資料）：

```bash
sudo mkdir -p /opt/animememes && sudo chown "$USER": /opt/animememes
git clone <repo URL> /opt/animememes
cd /opt/animememes
```

公開映像建置只需要 `compose.yaml`、`Dockerfile`、`deploy/`、`src/anime_memes/`、`requirements.txt`、`.dockerignore`；`config/` 只有 private target 才用，public build 不需要。

替代方案（VPS 不想放原始碼）：在本機建置後 `docker save` 再於 VPS `docker load`，但 compose 檔與 `deploy/` 仍需要放到 VPS，之後更新也要重做，一般不建議：

```powershell
docker compose --profile public build
docker save animememes-public-api animememes-public-nginx -o animememes-public.tar
scp animememes-public.tar myvps:/tmp/
```

```bash
docker load -i /tmp/animememes-public.tar && rm /tmp/animememes-public.tar
```

### 4.5 建立發布目錄

容器以 UID 10001 執行，發布目錄必須事先建好並由 10001 擁有（`install-bundle` 要寫入）：

```bash
sudo mkdir -p /srv/animememes/releases/{versions,media,reports}
sudo chown -R 10001:10001 /srv/animememes/releases
```

compose 對發布目錄的掛載設了 `create_host_path: false`：在 Linux 的 Docker Engine 上，目錄不存在時 `up` 會直接失敗，不會讓 Docker 自動建立 root 擁有的目錄。這個設定在 Windows／macOS 的 Docker Desktop 上不一定生效（實測 Windows 會照樣建立目錄），所以**一律先依上面的指令建立目錄並 chown**，不要依賴 Docker 自動建立。

### 4.6 建立 .env

在 `/opt/animememes/.env`：

```bash
umask 077
cat > .env <<'EOF'
CLOUDFLARE_TUNNEL_TOKEN=<tunnel token>
RELEASE_ROOT=/srv/animememes/releases
# 以下可選
# SEARCH_RATE=2r/s
# SEARCH_BURST=10
# PUBLIC_PORT=3637
# CLOUDFLARED_VERSION=2026.10.0
EOF
chmod 600 .env
```

不需要任何 R2、Discord 或 MCP 變數。`.env` 含秘密，不可提交。

### 4.7 建置與啟動

```bash
cd /opt/animememes
docker compose --profile public build
docker compose --profile public --profile tunnel up -d --wait
```

映像名稱固定為 `animememes-public-api`、`animememes-public-nginx`（compose 頂層 `name: animememes`）。

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
.\scripts\push_release.ps1 -Version VERSION -SshHost myvps -RemoteReleaseRoot /srv/animememes/releases -Activate
```

建議先加 `-DryRun` 看一遍將執行的命令。

3. 在 VPS 驗證：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3637/health/ready   # 應為 200
```

4. 瀏覽 <https://meme.jwander.net>，確認搜尋與縮圖正常。

## 6. 日常操作

### 6.1 發布新版本

本機匯出新版本後：

```powershell
.\scripts\push_release.ps1 -Version NEW_VERSION -SshHost myvps -RemoteReleaseRoot /srv/animememes/releases -Activate
```

不加 `-Activate` 只安裝並驗證，不切換目前版本；之後可在 VPS 以 `activate` 切換（見 6.3）。已存在的版本不會被覆寫。

### 6.2 程式更新

```bash
cd /opt/animememes
git pull
docker compose --profile public --profile tunnel up -d --build --wait
```

若更新包含公開資料結構（schema）版本升級，舊版本會因不相容而無法 ready，需在本機重新匯出並推送、啟用新版本。

**映像的安全更新**：主機的自動更新不會更新容器內的系統與套件。建議每月（或基底映像有安全公告時）重新拉取基底映像並重建：

```bash
cd /opt/animememes
docker compose --profile public --profile tunnel build --pull
docker compose --profile public --profile tunnel up -d --wait
docker image prune -f   # 清掉被取代的舊映像
```

cloudflared 的版本由 `.env` 的 `CLOUDFLARED_VERSION` 固定（預設見 compose.yaml）；要升級時改這個值後重新 `up -d`，確認連線正常。

### 6.3 回到舊版與其他 runtime 指令

在 VPS 以公開映像執行 runtime 指令（`--root /releases` 對應 `-v` 掛載）。回到舊版：

```bash
docker run --rm --user 10001:10001 \
  -v /srv/animememes/releases:/releases \
  animememes-public-api \
  python -m src.anime_memes.runtime rollback --root /releases OLD_VERSION
```

同樣方式可執行 `validate VERSION` 與 `activate VERSION`，只要把 `rollback --root /releases OLD_VERSION` 換成 `validate --root /releases VERSION` 或 `activate --root /releases VERSION`。切換不需要重啟服務。

### 6.4 清理舊版本

`prune` 需要先停公開服務，保留最新三個版本與目前啟用版本：

```bash
cd /opt/animememes
docker compose --profile public --profile tunnel stop
docker run --rm --user 10001:10001 \
  -v /srv/animememes/releases:/releases \
  animememes-public-api \
  python -m src.anime_memes.runtime prune --root /releases --service-stopped
docker compose --profile public --profile tunnel up -d --wait
```

停機期間，Cloudflare 快取中已版本化的 API 回應與縮圖仍可能被繼續提供；未快取的請求會失敗（Tunnel 離線）。不要在服務執行中清理。

## 7. 檢查與疑難排解

```bash
docker compose --profile public --profile tunnel ps
docker compose logs cloudflared
docker compose logs public-nginx public-api
```

- cloudflared 無法連線：看 `docker compose logs cloudflared`。token 空白或錯誤、VPS 無法連出，都會在這裡顯示。確認 `.env` 的 `CLOUDFLARE_TUNNEL_TOKEN` 正確且沒有多餘空白或引號。
- 網站 502／1033 等：先在 VPS 用 `curl http://127.0.0.1:3637/health/live` 確認 public-nginx 正常，再確認控制台 Public Hostname 的 Service 是 `HTTP` 與 `public-nginx:8080`。
- 429：搜尋限流依訪客 IP 計算。Nginx 只信任私有網段與 127.0.0.1 來源的 `CF-Connecting-IP` 作為真實 IP。若所有人都被一起限流，表示 Nginx 看到的是同一個來源 IP（例如沒有取到 `CF-Connecting-IP`），請檢查請求是否真的經過 Cloudflare／cloudflared，必要時暫時調高 `SEARCH_RATE`、`SEARCH_BURST` 並重建。
- 首次部署報目錄不存在（類似 `bind source path does not exist`）：`create_host_path: false` 的設計，代表 `RELEASE_ROOT` 或其下的 `media` 目錄還沒建立，或 `.env` 的路徑有誤。依 4.5 建立後再 `up`。
- `install-bundle` 權限錯誤：發布目錄需由 UID 10001 擁有（`chown -R 10001:10001`）。
- `/health/ready` 一直 503：尚未啟用任何版本，或版本與目前程式的 schema 不相容；用 `activate` 或重新匯出推送。
- 映像名稱：預設 `animememes-public-api` 與 `push_release.ps1` 的 `-Image` 預設一致。若用 `-p` 指定其他 compose 專案名稱，映像名稱會不同，需以 `-Image <名稱>` 指定，例如 `-Image animememes-public-api`。

## 8. 安全提醒

### 防護分層總覽

| 層級 | 已在程式／設定中完成 | 需自行設定 |
|---|---|---|
| Cloudflare 邊緣 | — | HTTPS、HSTS、最低 TLS 1.2（3.4）；WAF、邊緣限流、Bot（3.5）；快取規則（3.3） |
| 網路 | Tunnel 只連出，VPS IP 不公開；所有 port 只綁 127.0.0.1 | 防火牆只開 SSH（4.2） |
| VPS 主機 | — | SSH 只允許金鑰、自動安全更新（4.2）；定期重建映像（6.2） |
| Nginx | 依訪客真實 IP 限流（每秒 2 次、可瞬間多 10 次）；只開放必要路徑；請求上限 1 MB；CSP、X-Frame-Options、Referrer-Policy 等安全標頭；隱藏版本、不列目錄 | — |
| 公開 API | 只接受讀取；參數白名單；分頁、查詢長度（256 字／16 詞）、收藏數（200）上限；資料庫唯讀且只含已發布欄位 | — |
| 容器 | Python 服務以 UID 10001、Nginx 以 UID 101（nginx-unprivileged）執行；所有服務 `cap_drop: ALL`、禁止提權、根檔案系統唯讀（僅 tmpfs 與必要掛載可寫）；記憶體、CPU、程序數上限；日誌輪替；基底映像鎖定到明確版本（Python `3.12.15-slim-bookworm`、Nginx `1.30.6-alpine`） | — |

尚未處理、風險較低的強化項目：基底映像只鎖定標籤而未鎖定 digest、Python 間接依賴沒有完整鎖定檔。

### 其他提醒

- 管理端（3636）與 MCP 永不接上 Tunnel，也不要在 VPS 啟用 `private`、`mcp` profile。
- Tunnel token 外洩時，在 Cloudflare 控制台重設（refresh token），更新 VPS 的 `.env` 後重啟 cloudflared。
- VPS 只放公開資料（已發布的版本與縮圖）。管理庫與原圖只在本機。
- 待辦：管理庫與原圖的備份需在本機另外規劃，目前尚未建立異地備份。
