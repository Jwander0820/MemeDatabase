"use strict";
// 深淺色主題：在 <head> 同步載入，於首次繪製前套用使用者選擇，避免閃爍；公開頁與管理頁共用。
(() => {
  const root = document.documentElement;
  const media = matchMedia("(prefers-color-scheme: dark)");
  try {
    const saved = localStorage.getItem("meme-db-theme");
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch {}
  const isDark = () => root.dataset.theme ? root.dataset.theme === "dark" : media.matches;
  addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("themeToggle");
    const sync = () => {
      const dark = isDark();
      button.setAttribute("aria-pressed", String(dark));
      button.title = dark ? "切換為淺色模式" : "切換為深色模式";
      button.setAttribute("aria-label", button.title);
      // 手動切換後讓手機瀏覽器網址列顏色一起改變。
      document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
        meta.content = dark ? "#0f0f11" : "#f3f2ee";
      });
    };
    button.addEventListener("click", () => {
      root.dataset.theme = isDark() ? "light" : "dark";
      try { localStorage.setItem("meme-db-theme", root.dataset.theme); } catch {}
      sync();
    });
    media.addEventListener("change", sync);
    sync();
  });
})();
