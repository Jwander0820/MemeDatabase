"use strict";
// 深淺色主題：在 <head> 同步載入，於首次繪製前在 <html> 設定實際生效的 data-theme，避免閃爍；公開頁與管理頁共用。
// data-theme-source：user＝使用者手動選擇（存於 localStorage），system＝跟隨系統偏好。
(() => {
  const root = document.documentElement;
  const media = matchMedia("(prefers-color-scheme: dark)");
  let saved = null;
  try {
    const value = localStorage.getItem("meme-db-theme");
    if (value === "light" || value === "dark") saved = value;
  } catch {}
  const apply = (theme, source) => {
    root.dataset.theme = theme;
    root.dataset.themeSource = source;
  };
  const followSystem = () => apply(media.matches ? "dark" : "light", "system");
  if (saved) apply(saved, "user"); else followSystem();
  const isDark = () => root.dataset.theme === "dark";
  // meta theme-color 直接讀 CSS 的 --bg，避免與樣式表兩處色碼不同步。
  const FALLBACK_BG = { dark: "#0f0f11", light: "#f3f2ee" };
  const syncMeta = () => {
    const bg = getComputedStyle(root).getPropertyValue("--bg").trim() || FALLBACK_BG[root.dataset.theme];
    document.querySelectorAll('meta[name="theme-color"]').forEach(meta => { meta.content = bg; });
  };
  addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("themeToggle");
    const sync = () => {
      const dark = isDark();
      button.setAttribute("aria-pressed", String(dark));
      button.title = dark ? "切換為淺色模式" : "切換為深色模式";
      button.setAttribute("aria-label", button.title);
      // 手動切換後讓手機瀏覽器網址列顏色一起改變。
      syncMeta();
    };
    button.addEventListener("click", () => {
      apply(isDark() ? "light" : "dark", "user");
      try { localStorage.setItem("meme-db-theme", root.dataset.theme); } catch {}
      sync();
    });
    media.addEventListener("change", () => {
      if (root.dataset.themeSource !== "user") followSystem();
      sync();
    });
    sync();
  });
})();
