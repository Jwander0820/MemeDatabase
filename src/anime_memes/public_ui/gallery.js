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
  const link = element("a");
  link.className = "card";
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
  return link;
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
  tagRow.replaceChildren(chip("全部", ""), ...tags.map(item => chip(item.name, item.name, item.count)));
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
  for (const [name, value] of [["q", byId("query").value.trim()], ["tag", activeTag], ["sort", byId("sort").value]]) {
    if (value) params.set(name, value);
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
search(true);
showRoute();
