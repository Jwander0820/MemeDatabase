# commands

這裡放 Windows `.cmd` wrapper。它們不是主要程式碼，只是把常用的
`python -m src.anime_memes ...` 指令包成短入口，方便 LLM 或人工在需要時調用。

每個 `.cmd` 都會先切回專案根目錄，因此 `data/`、`exports/`、`.env` 等相對路徑仍以 repo root 為基準。

- `web.cmd`：啟動本地瀏覽與編輯 UI。
- `download_attachments.cmd`：補下載 Discord 原始附件。
- `build_thumbnails.cmd`：產生本地瀏覽用縮圖。
- `build_media.cmd`：產生 480P 縮圖與 720P 網頁版 WebP（`--kind thumb|web|all`）。
- `export_tagging_jobs.cmd`：匯出可讀圖 Agent 的待標籤工作。
- `import_tagging_results.cmd`：驗證並匯入 Agent 產生的候選標籤。
