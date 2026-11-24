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
let knownTags = [];
const tagRow = byId("tags");
const empty = byId("empty");

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
    results.replaceChildren();
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
      renderTags();
      tagsVersion = data.version;
    }
    version = data.version;
    page = data.page;
    results.append(...data.items.map(card));
    statusLine.textContent = data.total ? `已顯示 ${results.childElementCount}／${data.total} 張` : "查無結果";
    empty.hidden = data.total !== 0;
    more.textContent = "載入更多";
    more.hidden = results.childElementCount >= data.total;
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
  // 預設只載入本站縮圖；外部大圖（R2 720P 或手填網址）要等訪客明確點選才請求，避免每次開啟詳情都消耗外部主機流量。
  const image = element("img");
  image.alt = item.resolved_title;
  image.referrerPolicy = "no-referrer";
  const caption = element("figcaption");
  const note = element("span", item.original_url ? "目前顯示縮圖" : "僅提供縮圖");
  caption.append(note);
  image.onerror = () => {
    if (request !== detailRequest) return;
    note.textContent = "縮圖無法載入，請稍後再試。";
  };
  image.src = item.thumbnail_url;
  if (item.original_url) {
    const loadOriginal = button("載入大圖", () => {
      loadOriginal.disabled = true;
      note.textContent = "大圖載入中…";
      const full = new Image();
      full.referrerPolicy = "no-referrer";
      full.onload = () => {
        if (request !== detailRequest) return;
        image.onerror = null;
        image.src = full.src;
        note.textContent = "大圖";
        loadOriginal.remove();
      };
      full.onerror = () => {
        if (request !== detailRequest) return;
        note.textContent = "大圖無法載入，繼續顯示縮圖。";
        loadOriginal.disabled = false;
      };
      full.src = item.original_url;
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
        await fn();
        if (request === detailRequest) feedback.textContent = message;
      } catch {
        if (request === detailRequest) feedback.textContent = "瀏覽器或圖片來源限制了這項操作，可手動複製網址或下載本站縮圖。";
      }
    });
    if (primary) node.className = "primary";
    actions.append(node);
  }
  action("複製圖片", () => navigator.clipboard.write([new ClipboardItem({"image/png": pngBlob(url)})]), "已複製圖片，可直接貼上", true);
  action("複製頁面連結", () => navigator.clipboard.writeText(location.href), "已複製頁面連結");
  action("複製圖片連結", () => navigator.clipboard.writeText(url), "已複製圖片連結");
  if (item.original_url) {
    const original = element("a", "開啟大圖");
    original.href = url;
    original.target = "_blank";
    original.rel = "noreferrer";
    actions.append(original);
    action("下載大圖", async () => {
      const blob = await imageBlob(url);
      const download = element("a");
      download.href = URL.createObjectURL(blob);
      download.download = item.resolved_title;
      download.click();
      setTimeout(() => URL.revokeObjectURL(download.href), 60000);
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
  const match = /^#\/m\/([a-f0-9]{32})$/.exec(location.hash);
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
    const data = await readApi("entries/" + match[1], params);
    if (request !== detailRequest) return;
    feedback.textContent = "";
    renderDetail(data.item, request);
  } catch (error) {
    if (request !== detailRequest) return;
    feedback.textContent = error.message;
    byId("detail").append(button("從最新館藏重試", () => showRoute(true)));
  }
}

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
