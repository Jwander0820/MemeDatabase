# 動畫迷因資料庫

本地優先的動畫梗圖整理工具。

- **本機管理端**：從 Discord 匯入梗圖到 SQLite，下載附件、產生縮圖、OCR 輔助校驗、整理標籤，用 Web UI 搜尋、編輯、收藏與清理。
- **公開搜尋站**：只含管理端勾選發布的梗圖，提供搜尋、預覽、分享與取圖；訪客收藏與「我的梗圖」只存在自己的瀏覽器。
- **私人 MCP（選用）**：在本機提供唯讀查詢，公開站不含。

## 架構與資料流

```text
Discord ──DiscordChatExporter──> fixed JSON ──> SQLite（本機主庫）
                                                   │  附件、縮圖、OCR、標籤審核
                                                   ▼
                                  本機管理頁（3636，只綁 localhost）
                                                   │  選圖 → 檢查 → 匯出發布版本
                                                   ▼
                              發布包（公開 SQLite＋480P 縮圖；720P 大圖上 R2）
                                                   │  受限 SSH 推送、驗證後原子切換版本
                                                   ▼
訪客 ─HTTPS─> Cloudflare ─Tunnel─> VPS：public-nginx ─> public-api（唯讀）
```

- 管理端與原圖永遠留在本機；VPS 只跑公開 profile 與 Tunnel，不開連入 port。
- 程式與館藏分開更新：程式由 GitHub Actions 建置映像並部署；館藏由本機管理頁推送版本。
- 搜尋支援名稱、替代詞、繁簡／標點正規化、多詞跨欄位、`-排除`、`作品:詞`、引號片語與相關度排序。

## 快速開始（本機）

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-admin.txt
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

開啟 <http://127.0.0.1:3638>。增量同步 Discord：`src\commands\sync_discord.cmd`（需在 `.env` 設定 `DISCORD_BOT_TOKEN`）。所有指令見 `python -m src.anime_memes --help`。

用 Docker 時，私人管理頁在 <http://localhost:3636>、模擬公開頁在 <http://localhost:3637>，啟動方式見[本機 Docker 啟動](docs/backend-refactor.md#dockernginx-本機啟動)。

`data/`、`exports/`、`models/`、`.env`、`.venv/` 不提交到 Git。

## 部署概要

| 項目 | 方式 |
|---|---|
| 程式 | PR 跑 CI 測試；合併到 `main` 後 Actions 建置公開映像推到 GHCR，經受限 SSH 部署到 VPS，失敗自動恢復上一版 |
| 館藏 | 本機管理頁「發布管理 → 推送到 VPS」，增量傳縮圖；啟用後 VPS 自動清理舊版本，保留最新 3 版 |
| 依賴 | Python 版本以 `Dockerfile` 基底映像為準（目前 3.15，CI 也從這裡讀）；Docker 用含 SHA-256 的鎖檔；Dependabot 每月更新映像與 Actions |
| VPS 腳本 | 從 `raw.githubusercontent.com` 以 `curl` 下載後 `install`；VPS 上的 `/opt/memedatabase` 不是 git checkout |

## 文件

| 文件 | 內容 |
|---|---|
| [本機操作手冊](docs/local-operations.md) | 安裝、Web UI、CLI、批次發布、圖片尺寸與 R2、徹底撤回、測試 |
| [雙版本後端與發布操作](docs/backend-refactor.md) | 模組架構、發布包與熱更新、依賴鎖檔、Docker、私人 MCP |
| [VPS 部署手冊](docs/vps-deployment.md) | Cloudflare、VPS 首次部署、程式與館藏更新、回復與疑難排解 |
| [文件索引](docs/README.md) | 所有規格、操作手冊與歷史文件 |

## 測試

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/*.cjs
```

Docker 整合與 GPU 測試需另外開啟，見[本機操作手冊](docs/local-operations.md#測試)。

## 授權

程式碼與文件採用 [MIT License](LICENSE)。個人館藏、憑證、模型及第三方圖片不隨原始碼提供；第三方內容沿用各自的授權。
