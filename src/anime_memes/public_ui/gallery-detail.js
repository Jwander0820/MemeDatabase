"use strict";
// 詳情操作列用的線條圖示（24×24 viewBox，筆畫樣式由 CSS 的 .icon 控制）。
const ICONS = {
  copy: [["rect", {x: 8, y: 8, width: 13, height: 13, rx: 3}],
    ["path", {d: "M16 8V6a3 3 0 0 0-3-3H6a3 3 0 0 0-3 3v7a3 3 0 0 0 3 3h2"}]],
  download: [["path", {d: "M12 3v12m0 0-4.5-4.5M12 15l4.5-4.5M4 20h16"}]],
  star: [["path", {d: "M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"}]],
};

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  for (const [tag, attrs] of ICONS[name]) {
    const shape = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) shape.setAttribute(key, value);
    svg.append(shape);
  }
  return svg;
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

// 下載檔名：標題去掉檔案系統不允許的字元，副檔名依實際圖片格式。
function downloadName(title, type) {
  const extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/avif": "avif"};
  const base = title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").trim().slice(0, 100) || "meme";
  return base + "." + (extensions[type] || "webp");
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
  function action(label, iconName, fn, message, {primary = false} = {}) {
    const node = button("", async () => {
      try {
        const override = await fn();
        if (request === detailRequest) feedback.textContent = override || message;
      } catch {
        if (request === detailRequest) feedback.textContent = "瀏覽器或圖片來源限制了這項操作，可在圖片上按右鍵（手機長按）另存。";
      }
    });
    node.type = "button";
    node.append(icon(iconName));
    if (primary) {
      node.className = "primary";
      node.append(label);
    } else {
      // 次要操作只顯示圖示，文字放在提示與無障礙名稱。
      node.className = "icon-action";
      node.title = label;
      node.ariaLabel = label;
    }
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
  action("複製圖片", "copy", async () => {
    usedThumb = false;
    // 複製畫面上目前顯示的那張：縮圖狀態只複製縮圖；顯示原始圖片時優先用已載入的圖轉檔，不再下載。
    const source = !showingLarge ? pngBlob(thumbUrl)
      : largeImage ? elementPng(largeImage).catch(() => withFallback(pngBlob))
      : withFallback(pngBlob);
    // ClipboardItem 直接接收 Promise，Safari 才會保留使用者點擊的授權。
    await navigator.clipboard.write([new ClipboardItem({"image/png": source})]);
    recordUsage(item.id);
    if (!showingLarge && item.original_url) return "已複製縮圖；按「載入原始圖片」後可複製原始圖片";
    return usedThumb ? "原始圖片受來源限制，已改為複製縮圖" : "";
  }, "已複製圖片，可直接貼上", {primary: true});
  actions.append(favoriteButton(item));
  action("下載圖片", "download", async () => {
    usedThumb = false;
    // 下載畫面上目前顯示的那張（如同在圖片上右鍵另存）：縮圖狀態下載縮圖，顯示原始圖片時下載原始圖片。
    const blob = await (showingLarge ? withFallback(imageBlob) : imageBlob(thumbUrl));
    const download = element("a");
    download.href = URL.createObjectURL(blob);
    download.download = downloadName(item.resolved_title || item.id, blob.type);
    download.click();
    setTimeout(() => URL.revokeObjectURL(download.href), 60000);
    recordUsage(item.id);
    if (usedThumb) return "原始圖片受來源限制，已改為下載縮圖；可在圖片上按右鍵（手機長按）另存";
    return "";
  }, "已開始下載");
  const tags = element("div");
  tags.className = "detail-tags";
  tags.append(...item.tags.map(tag => {
    const chip = button(tag, () => {
      // 先拿掉詳情的 hash，關閉時就不會回上一頁（否則會與接著新增的搜尋紀錄互相干擾）。
      detailPushed = false;
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

// 詳情是從列表點開（新增了一筆 #/m/id 歷史）時為 true；關閉時用上一頁移除那筆，
// 才不會留下重複的紀錄，害「上一頁」要按兩次。直接開啟分享連結時為 false，關閉只改網址。
let detailPushed = false;

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
  const pushed = detailPushed;
  detailPushed = false;
  if (!/^#\/m\//.test(location.hash)) return;
  if (pushed) history.back();
  else history.replaceState(null, "", location.pathname + location.search);
});
window.addEventListener("hashchange", () => {
  const opening = !dialog.open && /^#\/m\//.test(location.hash);
  showRoute();
  if (opening) detailPushed = true;
});

