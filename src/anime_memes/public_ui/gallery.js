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
let activeTag = "";
// 本機簡潔瀏覽：由本機後端的 meta 宣告；公開 API 不回傳 mode，公開站不會出現管理入口。
let localMode = false;
let knownTags = [];
const tagRow = byId("tags");
const empty = byId("empty");

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
  node.className = compact ? "card-fav" : "fav-toggle";
  const sync = () => {
    const on = isFavorite(item);
    node.textContent = compact ? (on ? "★" : "☆") : (on ? "★ 已收藏" : "☆ 收藏");
    node.ariaPressed = String(on);
    node.title = on ? "取消收藏" : "加入收藏";
    if (compact) node.ariaLabel = node.title;
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

function card(item) {
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
  const source = element("div", [item.anime_title, item.speaker].filter(Boolean).join(" · "));
  source.className = "card-speaker";
  body.append(title, source);
  if (item.tags.length) {
    const tags = element("div");
    tags.className = "card-tags";
    tags.append(...item.tags.slice(0, 3).map(tag => element("span", tag)));
    body.append(tags);
  }
  link.append(image, body);
  node.append(link, favoriteButton(item, {compact: true}));
  return node;
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
  tagRow.replaceChildren(favorites, chip("全部", ""), ...tags.map(item => chip(item.name, item.name, item.count)));
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
    page = 0;
    version = "";
  }
  const params = new URLSearchParams({page: page + 1});
  const query = byId("query").value.trim();
  for (const [name, value] of [["q", query], ["tag", activeTag], ["sort", byId("sort").value]]) {
    if (value) params.set(name, value);
  }
  // field 只在有查詢字時才有意義。
  if (query && activeScope) params.set("field", activeScope);
  if (favoritesOnly) {
    if (localMode) params.set("favorite", "1");
    else params.set("ids", favoriteIds.slice(0, MAX_FAVORITE_QUERY).join(","));
  }
  if (version) params.set("version", version);
  more.hidden = true;
  empty.hidden = true;
  statusLine.textContent = "載入中…";
  try {
    const data = await readApi("entries", params);
    if (request !== listRequest) return;
    if (tagsVersion !== data.version) {
      const meta = await readApi("meta", new URLSearchParams({version: data.version}));
      if (request !== listRequest) return;
      knownTags = meta.tags;
      applyMode(meta);
      renderTags();
      tagsVersion = data.version;
    }
    version = data.version;
    page = data.page;
    const cards = data.items.map(card);
    if (masonry) masonry.append(cards);
    else results.append(...cards);
    shown += cards.length;
    statusLine.textContent = data.total ? `已顯示 ${shown}／${data.total} 張` : "查無結果";
    empty.hidden = data.total !== 0;
    byId("emptyTitle").textContent = favoritesOnly ? "還沒有收藏的梗圖" : "找不到符合的梗圖";
    byId("emptyHint").textContent = favoritesOnly ? "點卡片右上角的星號，常用的圖就會出現在這裡。" : "換個關鍵字，或試試其他標籤。";
    // 收藏的圖若已下架就查不到；只在沒有其他篩選時才能準確判斷少了幾張。
    if (favoritesOnly && !localMode && !activeTag && !byId("query").value.trim()) {
      const asked = Math.min(favoriteIds.length, MAX_FAVORITE_QUERY);
      if (asked > data.total) statusLine.textContent += `（另有 ${asked - data.total} 張收藏已下架）`;
    }
    more.textContent = "載入更多";
    more.hidden = shown >= data.total;
    retryFromStart = false;
  } catch (error) {
    if (request !== listRequest) return;
    statusLine.textContent = error.message;
    retryFromStart = page === 0 || error.status === 410;
    more.textContent = error.status === 410 ? "從最新館藏重新搜尋" : "重試";
    more.hidden = false;
  }
}

function button(label, action) {
  const node = element("button", label);
  node.onclick = action;
  return node;
}

async function imageBlob(url) {
  const response = await fetch(url, {referrerPolicy: "no-referrer"});
  if (!response.ok) throw new Error("無法讀取圖片");
  const blob = await response.blob();
  if (!blob.type.startsWith("image/")) throw new Error("無法讀取圖片");
  return blob;
}

async function pngBlob(url) {
  const bitmap = await createImageBitmap(await imageBlob(url));
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext("2d").drawImage(bitmap, 0, 0);
    return await new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(), "image/png"));
  } finally {
    bitmap.close();
  }
}

// 直接用畫面上已載入的圖轉成 PNG，不再下載一次；圖片需以 CORS 載入，否則畫布會被污染而無法輸出。
function elementPng(img) {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(), "image/png"));
}

function facts(rows) {
  const list = element("dl");
  list.className = "facts";
  for (const [label, value] of rows) {
    if (value) list.append(element("dt", label), element("dd", value));
  }
  return list;
}

function renderDetail(item, request) {
  const detail = byId("detail");
  const url = item.original_url || new URL(item.thumbnail_url, location.href).href;
  // 預設只載入本站縮圖；外部原始圖片（R2 720P 或手填網址）要等訪客明確點選才請求，避免每次開啟詳情都消耗外部主機流量。
  const image = element("img");
  image.alt = item.resolved_title;
  image.referrerPolicy = "no-referrer";
  const caption = element("figcaption");
  const direct = localMode && item.original_url;
  const note = element("span", direct ? "原始圖片" : item.original_url ? "目前顯示縮圖" : "僅提供縮圖");
  caption.append(note);
  image.onerror = () => {
    if (request !== detailRequest) return;
    note.textContent = "縮圖無法載入，請稍後再試。";
  };
  // 目前畫面顯示的是不是原始圖片：決定「複製圖片」要複製原始圖片還是縮圖，縮圖狀態下完全不碰外部原始圖片。
  let showingLarge = Boolean(direct);
  // 以 CORS 載入成功的原始圖片物件，可直接轉檔複製，不必重新下載。
  let largeImage = null;
  if (direct) {
    // 本機不需節省流量：直接顯示原圖，失敗才退回縮圖。
    image.onerror = () => {
      if (request !== detailRequest) return;
      image.onerror = null;
      showingLarge = false;
      note.textContent = "原始圖片無法載入，顯示縮圖。";
      image.src = item.thumbnail_url;
    };
    image.src = item.original_url;
  } else {
    image.src = item.thumbnail_url;
  }
  if (item.original_url && !direct) {
    // 先以 CORS 模式載入：來源有開放 CORS 時，之後複製可直接用這張圖；
    // 沒開放時改用一般模式載入，原始圖片照樣能看，只是複製會退回縮圖。
    const tryLoad = cors => {
      const full = new Image();
      full.referrerPolicy = "no-referrer";
      if (cors) full.crossOrigin = "anonymous";
      full.onload = () => {
        if (request !== detailRequest) return;
        image.onerror = null;
        // 顯示用的 img 與載入時同一模式，才能沿用瀏覽器快取而不重複下載。
        if (cors) image.crossOrigin = "anonymous";
        else image.removeAttribute("crossorigin");
        image.src = full.src;
        showingLarge = true;
        largeImage = cors ? full : null;
        note.textContent = "原始圖片";
        loadOriginal.remove();
      };
      full.onerror = () => {
        if (request !== detailRequest) return;
        if (cors) return tryLoad(false);
        note.textContent = "原始圖片無法載入，繼續顯示縮圖。";
        loadOriginal.disabled = false;
      };
      full.src = item.original_url;
    };
    const loadOriginal = button("載入原始圖片", () => {
      loadOriginal.disabled = true;
      note.textContent = "原始圖片載入中…";
      tryLoad(true);
    });
    loadOriginal.className = "load-original";
    caption.append(loadOriginal);
  }
  byId("media").replaceChildren(image, caption);
  byId("title").textContent = item.resolved_title;
  const actions = element("div");
  actions.className = "actions";
  function action(label, fn, message, primary) {
    const node = button(label, async () => {
      try {
        const override = await fn();
        if (request === detailRequest) feedback.textContent = override || message;
      } catch {
        if (request === detailRequest) feedback.textContent = "瀏覽器或圖片來源限制了這項操作，可手動複製網址或下載本站縮圖。";
      }
    });
    if (primary) node.className = "primary";
    actions.append(node);
  }
  // 外部原始圖片（R2）若未開放 CORS，fetch 會失敗；退回同網域的本站縮圖，讓複製／下載仍可用。
  const thumbUrl = new URL(item.thumbnail_url, location.href).href;
  let usedThumb = false;
  const withFallback = convert => convert(url).catch(error => {
    if (url === thumbUrl) throw error;
    usedThumb = true;
    return convert(thumbUrl);
  });
  action("複製圖片", async () => {
    usedThumb = false;
    // 複製畫面上目前顯示的那張：縮圖狀態只複製縮圖；顯示原始圖片時優先用已載入的圖轉檔，不再下載。
    const source = !showingLarge ? pngBlob(thumbUrl)
      : largeImage ? elementPng(largeImage).catch(() => withFallback(pngBlob))
      : withFallback(pngBlob);
    // ClipboardItem 直接接收 Promise，Safari 才會保留使用者點擊的授權。
    await navigator.clipboard.write([new ClipboardItem({"image/png": source})]);
    if (!showingLarge && item.original_url) return "已複製縮圖；按「載入原始圖片」後可複製原始圖片";
    return usedThumb ? "原始圖片受來源限制，已改為複製縮圖" : "";
  }, "已複製圖片，可直接貼上", true);
  actions.append(favoriteButton(item));
  action("複製頁面連結", () => navigator.clipboard.writeText(location.href), "已複製頁面連結");
  action("複製圖片連結", () => navigator.clipboard.writeText(url), "已複製圖片連結");
  if (item.original_url) {
    action("下載原始圖片", async () => {
      usedThumb = false;
      const blob = await withFallback(imageBlob);
      const download = element("a");
      download.href = URL.createObjectURL(blob);
      download.download = item.resolved_title;
      download.click();
      setTimeout(() => URL.revokeObjectURL(download.href), 60000);
      return usedThumb ? "原始圖片受來源限制，已改為下載縮圖；可用「複製圖片連結」在瀏覽器開啟後另存" : "";
    }, "已開始下載");
  }
  const thumb = element("a", "下載本站縮圖");
  thumb.href = item.thumbnail_url;
  thumb.download = item.id + ".webp";
  actions.append(thumb);
  const tags = element("div");
  tags.className = "detail-tags";
  tags.append(...item.tags.map(tag => {
    const chip = button(tag, () => {
      activeTag = tag;
      byId("query").value = "";
      renderTags();
      dialog.close();
      search(true);
    });
    chip.className = "chip";
    chip.title = "篩選標籤：" + tag;
    return chip;
  }));
  detail.replaceChildren(
    facts([["作品", item.anime_title], ["角色", item.speaker], ["台詞", item.quote_text]]),
    tags, actions);
}

async function showRoute(useLatest = false) {
  const request = ++detailRequest;
  const match = /^#\/m\/([A-Za-z0-9_.-]{1,128})$/.exec(location.hash);
  if (!match) {
    if (dialog.open) dialog.close();
    return;
  }
  if (!dialog.open) dialog.showModal();
  byId("title").textContent = "梗圖詳情";
  byId("media").replaceChildren();
  byId("detail").replaceChildren();
  feedback.textContent = "載入中…";
  const params = new URLSearchParams();
  if (!useLatest && version) params.set("version", version);
  try {
    const data = await readApi("entries/" + encodeURIComponent(match[1]), params);
    if (request !== detailRequest) return;
    feedback.textContent = "";
    renderDetail(data.item, request);
  } catch (error) {
    if (request !== detailRequest) return;
    feedback.textContent = error.message;
    byId("detail").append(button("從最新館藏重試", () => showRoute(true)));
  }
}

// 點框外（backdrop）關閉：按下與放開都在框外才算，避免框內拖曳選字時誤關。
// 以座標判斷是否在框外，點到框內捲軸時不會誤關。
const outsideBox = event => {
  if (event.target !== dialog) return false;
  const box = dialog.getBoundingClientRect();
  return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
};
let pressedOutside = false;
dialog.addEventListener("pointerdown", event => { pressedOutside = outsideBox(event); });
dialog.addEventListener("click", event => {
  if (pressedOutside && outsideBox(event)) dialog.close();
  pressedOutside = false;
});
dialog.addEventListener("close", () => {
  if (dialog.open) return;
  ++detailRequest;
  history.replaceState(null, "", location.pathname + location.search);
});
window.addEventListener("hashchange", () => showRoute());

byId("filters").onsubmit = event => { event.preventDefault(); search(true); };
byId("query").oninput = () => {
  ++listRequest;
  clearTimeout(timer);
  more.hidden = true;
  timer = setTimeout(() => search(true), 300);
};
byId("sort").onchange = () => search(true);
more.onclick = () => search(retryFromStart);
renderScope();
search(true);
showRoute();
