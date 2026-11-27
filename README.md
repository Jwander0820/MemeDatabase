# 動畫迷因資料庫

把平常在 Discord 收集的動畫梗圖整理成可以搜尋的圖庫，挑選後發布成一個公開的小網站，方便自己和朋友找圖、分享。

<!-- 截圖：之後可放公開站搜尋畫面，例如 docs/images/screenshot.png -->

## 可以做什麼

- **用文字找梗圖**：圖上的台詞經過 OCR 辨識再人工校對，可以直接搜台詞或作品名。
- **標籤分類**：每張圖有情緒、情境、用途等標籤，AI 會先給候選標籤，人工確認後才寫入。
- **公開搜尋站**：只放挑選過的梗圖，可以搜尋、預覽、複製或下載圖片、分享連結。
- **不用註冊**：訪客的收藏只存在自己的瀏覽器裡。
- **本地優先**：完整館藏和原圖都留在自己的電腦，公開站只拿到發布的那一份。

## 運作方式

```text
Discord ─匯出─> SQLite（本機主庫）
                  │  下載附件、縮圖、OCR、標籤審核
                  ▼
           本機管理頁（只綁 localhost）
                  │  挑選 → 檢查 → 匯出發布版本
                  ▼
           發布包（公開 SQLite + 縮圖；720P 大圖放 R2）
                  │  SSH 推送，驗證後切換版本
                  ▼
訪客 ─> Cloudflare ─Tunnel─> VPS：Nginx ─> 唯讀 API
```

---

## 技術重點

**技術棧**：Python、Flask、SQLite、原生 JavaScript、Docker、Nginx、Cloudflare Tunnel、Cloudflare R2、GitHub Actions。

- **管理端和公開站分開**：管理頁與原圖只在本機；VPS 只跑唯讀的公開服務，容器以 `read_only` 執行，也不對外開連入 port，流量全部經 Cloudflare Tunnel 進來。
- **程式和館藏分開更新**：程式由 CI 建置映像部署；館藏由本機管理頁推送版本，驗證通過才原子切換，保留最近幾版可以回復。
- **CI/CD**：PR 會跑測試；合併到 `main` 後先測試、再建置映像推到 GHCR，最後部署到 VPS，失敗會恢復上一版。
- **搜尋**：支援繁簡與標點正規化、多詞跨欄位、`-排除`、`作品:詞`、引號片語和相關度排序。
- **基本防護**：Nginx 對搜尋與圖片請求限流，限制搜尋併發數，靜態圖片走 Cloudflare 快取。

## 快速開始（本機）

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-admin.txt
.venv\Scripts\python.exe -m src.anime_memes --db data\anime_memes.sqlite3 web
```

開啟 <http://127.0.0.1:3638>。所有指令見 `python -m src.anime_memes --help`；用 Docker 啟動見[本機 Docker 啟動](docs/backend-refactor.md#dockernginx-本機啟動)。

測試：

```bat
.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/*.cjs
```

## 文件

- [本機操作手冊](docs/local-operations.md)：安裝、Web UI、CLI、發布、R2、測試
- [雙版本後端與發布操作](docs/backend-refactor.md)：模組架構、發布包、Docker、私人 MCP
- [VPS 部署手冊](docs/vps-deployment.md)：Cloudflare、首次部署、更新與回復
- [文件索引](docs/README.md)：所有規格與歷史文件

## 授權

程式碼與文件採用 [MIT License](LICENSE)。個人館藏、憑證、模型及第三方圖片不隨原始碼提供；第三方內容沿用各自的授權。
