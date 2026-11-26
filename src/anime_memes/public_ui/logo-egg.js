"use strict";
// 在首次繪製前還原角度；只記錄此瀏覽器的視覺偏好。
(() => {
  const key = "meme-db-logo-angle";
  const root = document.documentElement;
  let angle = -4;
  try {
    const saved = localStorage.getItem(key);
    if (saved !== null && /^(0|[1-9]\d*)$/.test(saved)) {
      const value = Number(saved);
      if (value < 360 && value % 15 === 0) angle = value;
    }
  } catch {}
  root.style.setProperty("--meme-logo-angle", `${angle}deg`);
  addEventListener("DOMContentLoaded", () => {
    let clicks = [];
    document.getElementById("home").addEventListener("click", event => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.detail === 0) return;
      const now = event.timeStamp;
      clicks = clicks.filter(time => now - time <= 2000);
      clicks.push(now);
      if (clicks.length < 7) return;
      clicks = [];
      angle = angle === -4 ? 0 : angle + 15;
      root.style.setProperty("--meme-logo-angle", `${angle}deg`);
      try { localStorage.setItem(key, String(angle % 360)); } catch {}
    });
  });
})();
