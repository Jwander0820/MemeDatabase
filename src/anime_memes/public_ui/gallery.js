"use strict";
const byId = id => document.getElementById(id);
const results = byId("results");
const statusLine = byId("status");
const more = byId("more");
const dialog = byId("preview");
const feedback = byId("feedback");
let page = 0;
let version = "";
let tagsVersion = "";
let listRequest = 0;
let detailRequest = 0;
let timer;
let retryFromStart = true;
// 捲到底自動載入的狀態；scroller 由 gallery-init.js 建立，不支援時維持 null 並退回「載入更多」按鈕。
let scroller = null;
let loading = false;
let loadFailed = false;
let hasMore = false;
const loadHint = byId("loadHint");
const canLoadMore = () => !loading && !loadFailed && hasMore && !dialog.open && !byId('publicContent')?.hidden;
let activeTag = "";
// 本機簡潔瀏覽：由本機後端的 meta 宣告；公開 API 不回傳 mode，公開站不會出現管理入口。
let localMode = false;
let knownTags = [];
const tagRow = byId("tags");
const empty = byId("empty");

// 瀑布流排列；沒有 MemeMasonry（例如單元測試）時退回直接附加。
const masonry = window.MemeMasonry?.create(results);
// 卡片分散在各欄裡，已顯示張數另外計算。
let shown = 0;

function element(name, text) {
  const node = document.createElement(name);
  if (text) node.textContent = text;
  return node;
}

async function readApi(path, params) {
  const response = await fetch("/api/v1/" + path + "?" + params);
  if (!response.ok) {
    const messages = {404: "此圖已下架或不存在", 410: "館藏版本已過期",
      429: "查詢過於頻繁，請稍後再試", 503: "館藏暫時無法使用"};
    const error = new Error(messages[response.status] || "讀取失敗，請重試");
    error.status = response.status;
    throw error;
  }
  return response.json();
}

const sameName = (a, b) => Boolean(a && b) && a.trim().toLowerCase() === b.trim().toLowerCase();

function card(item, {pinned = false} = {}) {
  // 卡片本體是連結；星號按鈕放在連結外面（連結裡不可再放按鈕）。
  const node = element("article");
  node.className = "card";
  const link = element("a");
  link.className = "card-link";
  link.href = "#/m/" + item.id;
  const image = element("img");
  image.src = item.thumbnail_url;
  image.alt = "";
  image.loading = "lazy";
  if (item.thumbnail_width && item.thumbnail_height) {
    image.width = item.thumbnail_width;
    image.height = item.thumbnail_height;
  }
  const body = element("div");
  body.className = "card-body";
  const title = element("strong", item.resolved_title);
  title.className = "card-title";
  // 同一個名字不重複出現：標題已含角色名就不再列角色，標籤略過與作品／角色同名者。
  const speaker = item.speaker && !(item.resolved_title || "").includes(item.speaker) ? item.speaker : "";
  const source = element("div", [item.anime_title, speaker].filter(Boolean).join(" · "));
  source.className = "card-speaker";
  body.append(title, source);
  const tagNames = item.tags.filter(tag => !sameName(tag, item.anime_title) && !sameName(tag, item.speaker));
  if (tagNames.length) {
    const tags = element("div");
    tags.className = "card-tags";
    tags.append(...tagNames.slice(0, 3).map(tag => element("span", tag)));
    body.append(tags);
  }
  link.append(image, body);
  node.append(link, favoriteButton(item, {compact: true}));
  if (pinned) {
    const badge = element("span", "常用");
    badge.className = "badge-pinned";
    node.append(badge);
  }
  return node;
}

// ---------- 首頁推薦 ----------
// 沒有搜尋字、標籤、收藏篩選且排序為「首頁推薦」時：前幾張是這位訪客常用的圖，其後是每日固定的隨機排列。
const PINNED_COUNT = 5;
let homeMode = false;
let homeSeed = "";
let pinnedIds = new Set();
let pinnedShown = 0;
// 後端已回傳（含被剔除的重複圖）的張數，用來判斷是否還有下一頁。
let fetched = 0;

const isHomeMode = () => !byId("query").value.trim() && !activeTag && !favoritesOnly && !byId("sort").value;

// 訪客當地時間的 YYYY-MM-DD；跨日後下次進首頁就換一種排列。
function localDate() {
  const now = new Date();
  const pad = value => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// 取得「常用」圖片：使用紀錄優先，不足再用收藏補；失敗或已下架的略過，永不拋出例外。
async function loadPinned() {
  const single = id => {
    const params = new URLSearchParams();
    if (version) params.set("version", version);
    return readApi("entries/" + encodeURIComponent(id), params).then(data => data.item, () => null);
  };
  const ids = topUsed(PINNED_COUNT);
  // 先同步送出單筆請求，之後才處理需要等待的收藏清單。
  const usedRequests = ids.map(single);
  const need = PINNED_COUNT - ids.length;
  let extra = [];
  if (need > 0) {
    if (localMode) {
      try {
        const data = await readApi("entries", new URLSearchParams({favorite: "1", sort: "updated", page_size: String(PINNED_COUNT)}));
        extra = (data.items || []).filter(item => !ids.includes(item.id)).slice(0, need).map(item => Promise.resolve(item));
      } catch {}
    } else {
      extra = favoriteIds.filter(id => !ids.includes(id)).slice(0, need).map(single);
    }
  }
  const items = (await Promise.all([...usedRequests, ...extra])).filter(Boolean);
  const seen = new Set();
  return items.filter(item => !seen.has(item.id) && seen.add(item.id));
}

function applyMode(meta) {
  if (meta.mode !== "local") return;
  localMode = true;
  const admin = byId("adminLink");
  admin.href = meta.admin_url || "/";
  admin.hidden = false;
  byId("home").href = meta.browse_url || location.pathname;
}

function renderTags() {
  const tags = knownTags.slice();
  if (activeTag && !tags.some(item => item.name === activeTag)) tags.push({name: activeTag});
  const chip = (label, value, count) => {
    const node = element("button", label);
    node.type = "button";
    node.className = "chip";
    node.ariaPressed = String(activeTag === value);
    if (count !== undefined) node.append(" ", element("small", String(count)));
    node.onclick = () => {
      activeTag = activeTag === value ? "" : value;
      renderTags();
      search(true);
    };
    return node;
  };
  // 「★ 收藏」是獨立的篩選，可以再搭配標籤或搜尋。
  const favorites = element("button", "★ 收藏");
  favorites.type = "button";
  favorites.className = "chip chip-fav";
  favorites.ariaPressed = String(favoritesOnly);
  favorites.onclick = () => {
    favoritesOnly = !favoritesOnly;
    renderTags();
    search(true);
  };
  // 「最近新增」與排序選單同步：選中即排序為 created，再點一次回到首頁推薦。
  const sortSelect = byId("sort");
  const recent = element("button", "最近新增");
  recent.type = "button";
  recent.className = "chip";
  recent.ariaPressed = String(sortSelect.value === "created");
  recent.onclick = () => {
    sortSelect.value = sortSelect.value === "created" ? "" : "created";
    renderTags();
    search(true);
  };
  tagRow.replaceChildren(favorites, recent, chip("全部", ""), ...tags.map(item => chip(item.name, item.name, item.count)));
  syncTagScroll();
}

// ---------- 標籤列捲動 ----------
// 標籤很多時只能左右捲動；依捲動位置切換兩端淡出與捲動鈕。
const tagStrip = byId("tagStrip");
function syncTagScroll() {
  if (!tagStrip.classList) return; // 單元測試的假 DOM 沒有 classList
  const max = tagRow.scrollWidth - tagRow.clientWidth;
  tagStrip.classList.toggle("can-prev", tagRow.scrollLeft > 1);
  tagStrip.classList.toggle("can-next", tagRow.scrollLeft < max - 1);
}
tagRow.addEventListener("scroll", syncTagScroll, {passive: true});
if (typeof ResizeObserver === "function") new ResizeObserver(syncTagScroll).observe(tagRow);
// 只有直向滾輪的滑鼠也能捲動：直向滾動轉成橫向，捲到盡頭後交還給頁面。
tagRow.addEventListener("wheel", event => {
  if (event.ctrlKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
  const delta = event.deltaY * (event.deltaMode === 1 ? 16 : 1);
  const max = tagRow.scrollWidth - tagRow.clientWidth;
  const next = Math.min(max, Math.max(0, tagRow.scrollLeft + delta));
  if (Math.abs(next - tagRow.scrollLeft) < 1) return;
  event.preventDefault();
  tagRow.scrollLeft = next;
}, {passive: false});
for (const [id, direction] of [["tagPrev", -1], ["tagNext", 1]]) {
  byId(id).onclick = () => {
    const reduce = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    tagRow.scrollBy({left: direction * tagRow.clientWidth * 0.8, behavior: reduce ? "auto" : "smooth"});
  };
}

// ---------- 手機頂欄收合 ----------
// 往下捲時收起頂欄、往上捲時再出現；is-hidden 只在窄螢幕的 CSS 生效，焦點在頂欄內時不收。
const topbar = document.querySelector?.(".topbar");
if (topbar?.classList) {
  let lastScrollY = window.scrollY;
  window.addEventListener("scroll", () => {
    const y = window.scrollY;
    if (Math.abs(y - lastScrollY) < 8) return;
    const hide = y > lastScrollY && y > topbar.offsetHeight && !topbar.contains(document.activeElement);
    topbar.classList.toggle("is-hidden", hide);
    lastScrollY = y;
  }, {passive: true});
  topbar.addEventListener("focusin", () => topbar.classList.remove("is-hidden"));
}

// 搜尋範圍：空字串為全部欄位，其餘對應 API 的 field 參數。
const SCOPES = [
  {value: "", label: "不限", placeholder: "搜尋台詞、作品、角色或標籤"},
  {value: "work", label: "作品", placeholder: "搜尋作品名稱"},
  {value: "speaker", label: "角色", placeholder: "搜尋角色"},
  {value: "quote", label: "台詞", placeholder: "搜尋台詞"},
];
let activeScope = "";

function renderScope() {
  const box = byId("scope");
  box.replaceChildren(...SCOPES.map(item => {
    const node = element("button", item.label);
    node.type = "button";
    node.className = "chip";
    node.ariaPressed = String(activeScope === item.value);
    node.onclick = () => setScope(item.value);
    return node;
  }));
  const current = SCOPES.find(item => item.value === activeScope) || SCOPES[0];
  byId("query").placeholder = current.placeholder;
}

function setScope(value) {
  if (value === activeScope) return;
  activeScope = value;
  renderScope();
  if (byId("query").value.trim()) search(true);
}

async function search(reset) {
  clearTimeout(timer);
  const request = ++listRequest;
  if (reset) {
    // 舊卡片已移除，清掉它們的星號同步記錄；詳情每次開啟都會重新登記。
    favoriteButtons.clear();
    if (masonry) masonry.reset();
    else results.replaceChildren();
    shown = 0;
    fetched = 0;
    page = 0;
    version = "";
    homeMode = isHomeMode();
    homeSeed = localDate();
    pinnedIds = new Set();
    pinnedShown = 0;
  }
  const params = new URLSearchParams({page: page + 1});
  const query = byId("query").value.trim();
  if (homeMode) {
    // 首頁：每日固定的隨機排列；後端會自動排除特定標籤。
    params.set("sort", "random");
    params.set("seed", homeSeed);
  } else {
    for (const [name, value] of [["q", query], ["tag", activeTag], ["sort", byId("sort").value]]) {
      if (value) params.set(name, value);
    }
    // field 只在有查詢字時才有意義。
    if (query && activeScope) params.set("field", activeScope);
    if (favoritesOnly) {
      if (localMode) params.set("favorite", "1");
      else params.set("ids", favoriteIds.slice(0, MAX_FAVORITE_QUERY).join(","));
    }
  }
  if (version) params.set("version", version);
  // 常用圖片只在首頁第一頁取得；先送出單筆請求，再送出隨機列表請求，兩者並行。
  const pinnedRequest = reset && homeMode ? loadPinned() : null;
  more.hidden = true;
  empty.hidden = true;
  statusLine.textContent = "載入中…";
  loading = true;
  loadFailed = false;
  hasMore = false;
  loadHint.textContent = scroller && !reset ? "載入中…" : "";
  try {
    const [data, pinned] = await Promise.all([readApi("entries", params), pinnedRequest]);
    if (request !== listRequest) return;
    if (tagsVersion !== data.version) {
      const meta = await readApi("meta", new URLSearchParams({version: data.version}));
      if (request !== listRequest) return;
      knownTags = meta.tags;
      const wasLocal = localMode;
      applyMode(meta);
      renderTags();
      tagsVersion = data.version;
      // 本機模式要到這裡才知道；使用紀錄與收藏的來源不同，首頁需以本機模式重新取得。
      if (!wasLocal && localMode && homeMode) {
        search(true);
        return;
      }
    }
    version = data.version;
    page = data.page;
    fetched += data.items.length;
    const pinnedItems = pinned || [];
    for (const item of pinnedItems) pinnedIds.add(item.id);
    pinnedShown += pinnedItems.length;
    // 隨機列表剔除已放在最前面的「常用」圖片。
    const cards = [
      ...pinnedItems.map(item => card(item, {pinned: true})),
      ...data.items.filter(item => !pinnedIds.has(item.id)).map(item => card(item)),
    ];
    if (masonry) masonry.append(cards);
    else results.append(...cards);
    shown += cards.length;
    if (data.total || shown) {
      const prefix = homeMode && pinnedShown ? `首頁推薦：常用 ${pinnedShown} 張＋每日隨機　` : "";
      statusLine.textContent = `${prefix}已顯示 ${shown}／${Math.max(data.total, shown)} 張`;
    } else {
      statusLine.textContent = "查無結果";
    }
    empty.hidden = data.total !== 0 || shown !== 0;
    byId("emptyTitle").textContent = favoritesOnly ? "還沒有收藏的梗圖" : "找不到符合的梗圖";
    byId("emptyHint").textContent = favoritesOnly ? "點卡片右上角的星號，常用的圖就會出現在這裡。" : "換個關鍵字，或試試其他標籤。";
    // 收藏的圖若已下架就查不到；只在沒有其他篩選時才能準確判斷少了幾張。
    if (favoritesOnly && !localMode && !activeTag && !byId("query").value.trim()) {
      const asked = Math.min(favoriteIds.length, MAX_FAVORITE_QUERY);
      if (asked > data.total) statusLine.textContent += `（另有 ${asked - data.total} 張收藏已下架）`;
    }
    const favoriteNotice = favoriteListNotice();
    if (favoriteNotice) statusLine.textContent += ` ${favoriteNotice}`;
    hasMore = fetched < data.total;
    more.textContent = "載入更多";
    // 有自動載入時不顯示按鈕；全部載完則在列表底部顯示提示（查無結果不顯示）。
    more.hidden = !hasMore || Boolean(scroller);
    loadHint.textContent = !hasMore && shown ? "已經到底了" : "";
    retryFromStart = false;
  } catch (error) {
    if (request !== listRequest) return;
    statusLine.textContent = error.message;
    retryFromStart = page === 0 || error.status === 410;
    loadFailed = true;
    loadHint.textContent = "";
    more.textContent = error.status === 410 ? "從最新館藏重新搜尋" : "重試";
    more.hidden = false;
  } finally {
    if (request === listRequest) {
      loading = false;
      // 哨兵仍在視窗內時繼續載入；失敗時 canLoadMore 為 false，不會自動重試。
      scroller?.check();
    }
  }
}

