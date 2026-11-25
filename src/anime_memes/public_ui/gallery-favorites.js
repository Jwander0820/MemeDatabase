"use strict";
// ---------- 收藏 ----------
// 公開站：訪客沒有帳號，收藏存在自己瀏覽器的 localStorage（清除瀏覽器資料就會消失）。
// 本機簡潔瀏覽：與管理頁共用同一份收藏（後端 entry_favorites）。
const FAVORITES_KEY = "meme-db-favorites";
const MAX_FAVORITE_QUERY = 200;
let favoritesOnly = false;
let favoriteIds = [];
try {
  const saved = JSON.parse(globalThis.localStorage?.getItem(FAVORITES_KEY) || "[]");
  if (Array.isArray(saved)) favoriteIds = saved.filter(id => typeof id === "string");
} catch {}
// 同一張圖可能同時出現在卡片與詳情，切換後要一起更新。
const favoriteButtons = new Map();

function isFavorite(item) {
  return localMode ? Boolean(item.favorite) : favoriteIds.includes(item.id);
}

async function toggleFavorite(item) {
  if (localMode) {
    const response = await fetch("/api/entries/" + encodeURIComponent(item.id) + "/favorite",
      {method: item.favorite ? "DELETE" : "POST"});
    if (!response.ok) throw new Error("收藏更新失敗，請重試");
    item.favorite = (await response.json()).favorite;
  } else {
    // 最新收藏放最前面。
    favoriteIds = isFavorite(item) ? favoriteIds.filter(id => id !== item.id) : [item.id, ...favoriteIds];
    try { globalThis.localStorage?.setItem(FAVORITES_KEY, JSON.stringify(favoriteIds)); } catch {}
  }
  (favoriteButtons.get(item.id) || []).forEach(sync => sync());
  return isFavorite(item);
}

function favoriteButton(item, {compact = false} = {}) {
  const node = element("button");
  node.type = "button";
  node.className = compact ? "card-fav" : "fav-toggle icon-action";
  // 詳情用圖示按鈕，實心／空心星號由 CSS 依 aria-pressed 切換。
  if (!compact) node.append(icon("star"));
  const sync = () => {
    const on = isFavorite(item);
    if (compact) node.textContent = on ? "★" : "☆";
    node.ariaPressed = String(on);
    node.title = on ? "取消收藏" : "加入收藏";
    node.ariaLabel = node.title;
  };
  node.onclick = async event => {
    event.preventDefault();
    event.stopPropagation();
    node.disabled = true;
    try {
      await toggleFavorite(item);
    } catch (error) {
      statusLine.textContent = error.message;
    } finally {
      node.disabled = false;
    }
  };
  const list = favoriteButtons.get(item.id) || [];
  list.push(sync);
  favoriteButtons.set(item.id, list);
  sync();
  return node;
}
