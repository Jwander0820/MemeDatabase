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

async function search(reset) {
  clearTimeout(timer);
  const request = ++listRequest;
  if (reset) {
    results.replaceChildren();
    page = 0;
    version = "";
  }
  const params = new URLSearchParams({page: page + 1});
  for (const [name, id] of [["q", "query"], ["tag", "tag"], ["sort", "sort"]]) {
    const value = byId(id).value.trim();
    if (value) params.set(name, value);
  }
  if (version) params.set("version", version);
  more.hidden = true;
  statusLine.textContent = "載入中…";
  try {
    const data = await readApi("entries", params);
    if (request !== listRequest) return;
    if (tagsVersion !== data.version) {
      const meta = await readApi("meta", new URLSearchParams({version: data.version}));
      if (request !== listRequest) return;
      const tag = byId("tag");
      const selected = tag.value;
      tag.replaceChildren(new Option("全部標籤", ""));
      for (const item of meta.tags) tag.add(new Option(item.name + "（" + item.count + "）", item.name));
      if (selected && !meta.tags.some(item => item.name === selected)) tag.add(new Option(selected, selected));
      tag.value = selected;
      tagsVersion = data.version;
    }
    version = data.version;
    page = data.page;
    results.append(...data.items.map(card));
    statusLine.textContent = data.total ? `已顯示 ${results.childElementCount}／${data.total} 張` : "查無結果";
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

function renderDetail(item, request) {
  const detail = byId("detail");
  const url = item.original_url || new URL(item.thumbnail_url, location.href).href;
  const image = element("img");
  image.alt = item.resolved_title;
  const caption = element("p", item.original_url ? "原圖預覽" : "僅提供縮圖");
  image.referrerPolicy = "no-referrer";
  image.onerror = () => {
    if (request !== detailRequest) return;
    image.onerror = null;
    if (item.original_url) {
      caption.textContent = "原圖無法載入，顯示本站縮圖。";
      image.src = item.thumbnail_url;
    } else {
      caption.textContent = "縮圖無法載入，請稍後再試。";
    }
  };
  image.src = url;
  byId("title").textContent = item.resolved_title;
  const actions = element("div");
  actions.className = "actions";
  function action(label, fn, message) {
    actions.append(button(label, async () => {
      try {
        await fn();
        if (request === detailRequest) feedback.textContent = message;
      } catch {
        if (request === detailRequest) feedback.textContent = "瀏覽器或圖片來源限制了這項操作，可手動複製網址或下載本站縮圖。";
      }
    }));
  }
  action("複製頁面連結", () => navigator.clipboard.writeText(location.href), "已複製頁面連結");
  action("複製圖片連結", () => navigator.clipboard.writeText(url), "已複製圖片連結");
  action("複製圖片", () => navigator.clipboard.write([new ClipboardItem({"image/png": pngBlob(url)})]), "已複製圖片");
  if (item.original_url) {
    const original = element("a", "開啟原圖");
    original.href = url;
    original.target = "_blank";
    original.rel = "noreferrer";
    actions.append(original);
    action("下載原圖", async () => {
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
  detail.replaceChildren(image, caption,
    element("p", [item.anime_title, item.speaker, item.quote_text].filter(Boolean).join(" · ")),
    element("p", item.tags.join("、")), actions);
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
byId("tag").onchange = byId("sort").onchange = () => search(true);
more.onclick = () => search(retryFromStart);
search(true);
showRoute();
