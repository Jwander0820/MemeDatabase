function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

let toastTimer = 0;

// 顯示時間依文字長度：至少 3 秒，每字多 40ms，上限 6 秒；新訊息會取消舊計時器。
function showToast(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  const duration = Math.min(6000, 3000 + String(message).length * 40);
  toastTimer = setTimeout(() => toast.classList.remove("show"), duration);
}

// 管理 API 的錯誤格式：{"error": {"code", "message"}}；相容舊的 {"error": "訊息"}。
function errorMessage(body, fallback) {
  const error = body?.error;
  if (typeof error === "string" && error) return error;
  if (error && typeof error.message === "string" && error.message) return error.message;
  return fallback;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function attr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}
