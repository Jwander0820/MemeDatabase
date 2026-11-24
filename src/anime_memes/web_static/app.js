const T = {
  missingImage: "\u7f3a\u5716\u7247",
  unknownTitle: "\u672a\u586b\u4f5c\u54c1",
  unknownSpeaker: "\u672a\u586b\u8aaa\u8a71\u8005",
  noPreview: "\u6c92\u6709\u53ef\u9810\u89bd\u5716\u7247",
  title: "\u4f5c\u54c1\u540d\u7a31",
  speaker: "\u8aaa\u8a71\u8005",
  quote: "\u5716\u7247\u53f0\u8a5e",
  tags: "\u6a19\u7c64\uff08\u9017\u865f\u5206\u9694\uff09",
  status: "\u72c0\u614b",
  notes: "\u5099\u8a3b",
  copyQuote: "\u8907\u88fd\u53f0\u8a5e",
  save: "\u5132\u5b58\u4fee\u6539",
  deleteEntry: "\u6c38\u4e45\u522a\u9664",
  sourceTime: "\u4f86\u6e90\u6642\u9593",
  file: "\u6a94\u6848",
  link: "\u9023\u7d50",
  openOriginal: "\u958b\u555f\u539f\u5716",
  download: "\u4e0b\u8f09",
  size: "\u5c3a\u5bf8",
  ocrText: "OCR \u6587\u5b57",
  copied: "\u5df2\u8907\u88fd\u5230\u526a\u8cbc\u7c3f",
  saveFailed: "\u5132\u5b58\u5931\u6557",
  tagsFailed: "\u6a19\u7c64\u5132\u5b58\u5931\u6557",
  saved: "\u5df2\u5132\u5b58",
  deleteConfirm: "\u6c38\u4e45\u522a\u9664\u9019\u7b46\u8cc7\u6599\u8207\u672c\u5730\u5716\u7247\uff1f\u6b64\u64cd\u4f5c\u4e0d\u6703\u9032\u56de\u6536\u5340\u3002",
  deleteFailed: "\u522a\u9664\u5931\u6557",
  deleted: "\u5df2\u6c38\u4e45\u522a\u9664",
  created: "\u5df2\u65b0\u589e\u5230\u8cc7\u6599\u5eab",
  createFailed: "\u65b0\u589e\u5931\u6557\uff0c\u8acb\u6aa2\u67e5\u5716\u7247\u8207\u6b04\u4f4d",
  suggestions: "AI\uff0fAgent \u5019\u9078\u6a19\u7c64",
  accept: "\u63a5\u53d7",
  reject: "\u62d2\u7d55",
  decisionFailed: "\u5019\u9078\u6a19\u7c64\u5be9\u6838\u5931\u6557",
  manualTags: "人工標籤（逗號分隔）",
  decisionRefreshFailed: "審核已完成，清單更新失敗，請重新整理",
};

const state = {
  page: 1,
  pageSize: 48,
  total: 0,
  tag: "",
  items: [],
  selected: null,
  randomSeed: String(Math.random()),
  entriesRequest: 0,
  loading: false,
};

const grid = document.querySelector("#grid");
const resultCount = document.querySelector("#resultCount");
const searchInput = document.querySelector("#searchInput");
const statusFilter = document.querySelector("#statusFilter");
const sortFilter = document.querySelector("#sortFilter");
const favoriteOnly = document.querySelector("#favoriteOnly");
const missingImage = document.querySelector("#missingImage");
const noTags = document.querySelector("#noTags");
const tagList = document.querySelector("#tagList");
const clearTag = document.querySelector("#clearTag");
const loadMore = document.querySelector("#loadMore");
const drawer = document.querySelector("#drawer");
const drawerBody = document.querySelector("#drawerBody");
const closeDrawer = document.querySelector("#closeDrawer");
const favoriteButton = document.querySelector("#favoriteButton");
const openCreate = document.querySelector("#openCreate");
const createDialog = document.querySelector("#createDialog");
const createForm = document.querySelector("#createForm");
const createImage = document.querySelector("#createImage");
const createPreview = document.querySelector("#createPreview");
const createError = document.querySelector("#createError");
const closeCreate = document.querySelector("#closeCreate");
const cancelCreate = document.querySelector("#cancelCreate");
const saveCreate = document.querySelector("#saveCreate");
const toast = document.querySelector("#toast");
let createPreviewUrl = "";

function params(page = state.page) {
  const search = new URLSearchParams();
  search.set("page", page);
  search.set("page_size", state.pageSize);
  search.set("status", statusFilter.value);
  search.set("sort", sortFilter.value);
  search.set("seed", state.randomSeed);
  if (searchInput.value.trim()) search.set("q", searchInput.value.trim());
  if (state.tag) search.set("tag", state.tag);
  if (favoriteOnly.checked) search.set("favorite", "1");
  if (missingImage.checked) search.set("missing_image", "1");
  if (noTags.checked) search.set("no_tags", "1");
  return search;
}

async function loadEntries(reset = false) {
  if (!reset && state.loading) return;
  const requestId = ++state.entriesRequest;
  state.loading = true;
  loadMore.disabled = true;
  try {
    const response = await fetch(`/api/entries?${params(reset ? 1 : state.page + 1)}`);
    if (!response.ok) throw new Error("entries request failed");
    const data = await response.json();
    if (requestId !== state.entriesRequest) return;
    state.page = data.page;
    state.total = data.total;
    state.items = reset ? data.items : state.items.concat(data.items);
    renderGrid();
  } catch {
    if (requestId === state.entriesRequest) showToast("清單載入失敗，請重試");
  } finally {
    if (requestId === state.entriesRequest) {
      state.loading = false;
      loadMore.disabled = false;
    }
  }
}

async function loadMeta() {
  const response = await fetch("/api/meta");
  const data = await response.json();
  tagList.innerHTML = "";
  data.tags.forEach((tag) => {
    const button = document.createElement("button");
    button.className = `tag ${state.tag === tag.name ? "active" : ""}`;
    button.textContent = `${tag.name} ${tag.count}`;
    button.type = "button";
    button.addEventListener("click", () => {
      state.tag = state.tag === tag.name ? "" : tag.name;
      loadMeta();
      loadEntries(true);
    });
    tagList.appendChild(button);
  });
}

function renderGrid() {
  grid.innerHTML = "";
  resultCount.textContent = `${state.items.length} / ${state.total}`;
  state.items.forEach((item) => grid.appendChild(card(item)));
  loadMore.disabled = state.items.length >= state.total;
}

function card(item) {
  const node = document.createElement("article");
  node.className = "card";
  node.tabIndex = 0;
  const image = item.thumbnail_url || item.image_url;
  const tags = item.tags.slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  node.innerHTML = `
    ${image ? `<img src="${attr(image)}" alt="">` : `<div class="missing-image">${T.missingImage}</div>`}
    <div class="card-body">
      <div class="card-title">
        <span>${escapeHtml(item.anime_title || T.unknownTitle)}</span>
        <span>${item.favorite ? "\u2605" : ""}</span>
      </div>
      <div class="card-speaker">${escapeHtml(item.speaker || T.unknownSpeaker)}</div>
      <p class="card-quote">${escapeHtml(item.quote_text || "")}</p>
      <div class="card-tags">${tags}</div>
    </div>
  `;
  node.addEventListener("click", () => openEntry(item.key));
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter") openEntry(item.key);
  });
  return node;
}

async function openEntry(key) {
  const response = await fetch(`/api/entries/${encodeURIComponent(key)}`);
  if (!response.ok) {
    closeDetail();
    await loadEntries(true);
    return;
  }
  const item = await response.json();
  state.selected = item;
  favoriteButton.textContent = item.favorite ? "\u2605" : "\u2606";
  drawerBody.innerHTML = detailTemplate(item);
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  bindDetailForm(item);
  bindPublication(drawerBody.querySelector("#entryPublication"), item.key);
}

function detailTemplate(item) {
  const image = item.image_url || item.thumbnail_url;
  const suggestions = suggestionTemplate(item.tag_suggestions || []);
  return `
    ${image ? `<img class="drawer-image" src="${attr(image)}" alt="">` : `<div class="missing-image">${T.noPreview}</div>`}
    <div class="card-tags" id="detailTags">${item.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
    ${suggestions}
    <form class="edit-form" id="editForm">
      <label class="field"><span>${T.title}</span><input name="anime_title" value="${attr(item.anime_title || "")}"></label>
      <label class="field"><span>${T.speaker}</span><input name="speaker" value="${attr(item.speaker || "")}"></label>
      <label class="field"><span>${T.quote}</span><textarea name="quote_text">${escapeHtml(item.quote_text || "")}</textarea></label>
      <label class="field"><span>${T.manualTags}</span><input name="tags" value="${attr((item.manual_tags || []).join(", "))}"></label>
      <label class="field"><span>${T.status}</span>
        <select name="status">
          <option value="ready" ${item.status === "ready" ? "selected" : ""}>ready</option>
          <option value="ignored" ${item.status === "ignored" ? "selected" : ""}>ignored</option>
          <option value="needs_review" ${item.status === "needs_review" ? "selected" : ""}>needs_review</option>
        </select>
      </label>
      <label class="field"><span>${T.notes}</span><textarea name="notes">${escapeHtml(item.notes || "")}</textarea></label>
      <div class="edit-actions">
        <button type="button" id="copyQuote">${T.copyQuote}</button>
        <button type="button" id="deleteEntry" class="danger">${T.deleteEntry}</button>
        <button class="primary" type="submit">${T.save}</button>
      </div>
    </form>
    <form id="entryPublication" class="edit-form">
      <fieldset disabled>
        <legend>公開設定</legend>
        <label><input type="checkbox" name="selected"> 納入公開館藏</label>
        <label class="field">公開原圖網址（選填）<input name="original" type="url" placeholder="https://"></label>
        <button type="submit">儲存公開設定</button>
      </fieldset>
      <output role="status">正在讀取公開設定…</output>
    </form>
    <div class="meta-box">
      Key: ${escapeHtml(item.key)}<br>
      ${T.sourceTime}: ${escapeHtml(item.source_timestamp || "")}<br>
      ${T.file}: ${escapeHtml(item.file_name || "")}<br>
      ${T.link}: ${item.attachment_url ? `<a href="${attr(item.attachment_url)}" target="_blank" rel="noreferrer">${T.openOriginal}</a><br>` : ""}
      ${T.download}: ${escapeHtml(item.download_status || "")}<br>
      ${T.size}: ${escapeHtml(String(item.image_width || ""))} x ${escapeHtml(String(item.image_height || ""))}<br>
      OCR: ${escapeHtml(item.ocr_status || "")}<br>
      ${item.ocr_text ? `${T.ocrText}:<br>${escapeHtml(item.ocr_text)}` : ""}
    </div>
  `;
}

function suggestionTemplate(suggestions) {
  if (!suggestions.length) return "";
  const items = suggestions.map((suggestion) => `
    <article class="suggestion-card">
      <div class="suggestion-heading">
        <strong>${escapeHtml(suggestion.label)}</strong>
        <span>${escapeHtml(suggestion.category)} \u00b7 ${Math.round(Number(suggestion.confidence) * 100)}%</span>
      </div>
      <p>${escapeHtml(suggestion.reason)}</p>
      <small>${escapeHtml(suggestion.actor_name)}${suggestion.model_name ? ` \u00b7 ${escapeHtml(suggestion.model_name)}` : ""}</small>
      <small>${escapeHtml(suggestion.created_at || "")}</small>
      <div class="suggestion-actions">
        <button type="button" data-suggestion-id="${suggestion.id}" data-decision="rejected">${T.reject}</button>
        <button type="button" class="primary" data-suggestion-id="${suggestion.id}" data-decision="accepted">${T.accept}</button>
      </div>
    </article>
  `).join("");
  return `<section class="suggestions"><h3>${T.suggestions}</h3>${items}</section>`;
}

function bindDetailForm(item) {
  const form = document.querySelector("#editForm");
  document.querySelectorAll("[data-suggestion-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const card = button.closest(".suggestion-card");
      const actions = card.querySelectorAll("button");
      actions.forEach((action) => { action.disabled = true; });
      try {
        const response = await fetch(`/api/tag-suggestions/${button.dataset.suggestionId}/decision`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({decision: button.dataset.decision, reviewed_by: "local_user"}),
        });
        if (!response.ok) throw new Error(T.decisionFailed);
        const decision = await response.json();
        showToast(decision.status === "accepted" ? "已接受候選標籤" : "已拒絕候選標籤");
        // Update only the reviewed card: replacing the drawer would discard drafts.
        if (card.isConnected && state.selected?.key === item.key) {
          const section = card.closest(".suggestions");
          const nextButton = section.querySelector("[data-suggestion-id]:not(:disabled)");
          card.remove();
          if (!section.querySelector(".suggestion-card")) section.remove();
          item.tag_suggestions = item.tag_suggestions.filter((suggestion) => suggestion.id !== decision.id);
          if (decision.status === "accepted") item.tags = [...new Set([...item.tags, decision.label])];
          document.querySelector("#detailTags").innerHTML = item.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
          (nextButton || form.anime_title).focus();
        }
      } catch {
        showToast(T.decisionFailed);
        return;
      } finally {
        actions.forEach((action) => { action.disabled = false; });
      }
      try {
        await Promise.all([loadMeta(), loadEntries(true)]);
      } catch {
        showToast(T.decisionRefreshFailed);
      }
    });
  });
  document.querySelector("#copyQuote").addEventListener("click", async () => {
    await navigator.clipboard.writeText(form.quote_text.value);
    showToast(T.copied);
  });
  document.querySelector("#deleteEntry").addEventListener("click", async () => {
    if (!confirm(`${T.deleteConfirm}\n\n${item.key}`)) return;
    const response = await fetch(`/api/entries/${encodeURIComponent(item.key)}`, {method: "DELETE"});
    if (!response.ok) return showToast(T.deleteFailed);
    closeDetail();
    await loadMeta();
    await loadEntries(true);
    showToast(T.deleted);
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      anime_title: form.anime_title.value,
      speaker: form.speaker.value,
      quote_text: form.quote_text.value,
      status: form.status.value,
      notes: form.notes.value,
    };
    const saved = await fetch(`/api/entries/${encodeURIComponent(item.key)}`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!saved.ok) return showToast(T.saveFailed);
    const tags = await fetch(`/api/entries/${encodeURIComponent(item.key)}/tags`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({tags: form.tags.value}),
    });
    if (!tags.ok) return showToast(T.tagsFailed);
    showToast(T.saved);
    await loadMeta();
    await loadEntries(true);
    await openEntry(item.key);
  });
}

favoriteButton.addEventListener("click", async () => {
  if (!state.selected || favoriteButton.disabled) return;
  const item = state.selected;
  const method = item.favorite ? "DELETE" : "POST";
  favoriteButton.disabled = true;
  try {
    const response = await fetch(`/api/entries/${encodeURIComponent(item.key)}/favorite`, {method});
    if (!response.ok) throw new Error("favorite failed");
    const result = await response.json();
    item.favorite = result.favorite;
    if (state.selected?.key === item.key) {
      state.selected.favorite = result.favorite;
      favoriteButton.textContent = result.favorite ? "\u2605" : "\u2606";
    }
    await loadEntries(true);
  } catch {
    showToast("收藏更新失敗，請重試");
  } finally {
    favoriteButton.disabled = false;
  }
});

closeDrawer.addEventListener("click", closeDetail);

openCreate.addEventListener("click", () => {
  createError.textContent = "";
  createDialog.showModal();
});

[closeCreate, cancelCreate].forEach((button) => {
  button.addEventListener("click", () => createDialog.close());
});

createImage.addEventListener("change", () => {
  if (createPreviewUrl) URL.revokeObjectURL(createPreviewUrl);
  const file = createImage.files[0];
  if (!file) {
    createPreview.removeAttribute("src");
    createPreview.closest(".upload-field").classList.remove("has-preview");
    return;
  }
  createPreviewUrl = URL.createObjectURL(file);
  createPreview.src = createPreviewUrl;
  createPreview.closest(".upload-field").classList.add("has-preview");
  createError.textContent = "";
});

createDialog.addEventListener("close", resetCreateForm);

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  createError.textContent = "";
  saveCreate.disabled = true;
  try {
    const response = await fetch("/api/entries/manual", {
      method: "POST",
      body: new FormData(createForm),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      createError.textContent = data.error || T.createFailed;
      return;
    }
    createDialog.close();
    searchInput.value = "";
    statusFilter.value = data.status;
    sortFilter.value = "updated";
    favoriteOnly.checked = false;
    missingImage.checked = false;
    noTags.checked = false;
    state.tag = "";
    await loadMeta();
    await loadEntries(true);
    await openEntry(data.key);
    showToast(T.created);
  } catch (_error) {
    createError.textContent = T.createFailed;
  } finally {
    saveCreate.disabled = false;
  }
});

function resetCreateForm() {
  createForm.reset();
  createError.textContent = "";
  if (createPreviewUrl) URL.revokeObjectURL(createPreviewUrl);
  createPreviewUrl = "";
  createPreview.removeAttribute("src");
  createPreview.closest(".upload-field").classList.remove("has-preview");
}

function closeDetail() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  state.selected = null;
  favoriteButton.textContent = "\u2606";
  drawerBody.innerHTML = "";
}

loadMore.addEventListener("click", () => {
  loadEntries(false);
});

[searchInput, statusFilter, sortFilter, favoriteOnly, missingImage, noTags].forEach((el) => {
  el.addEventListener(el === searchInput ? "input" : "change", el === searchInput
    ? debounce(() => loadEntries(true), 180)
    : () => {
      if (el === sortFilter) state.randomSeed = String(Math.random());
      loadEntries(true);
    });
});

clearTag.addEventListener("click", () => {
  state.tag = "";
  loadMeta();
  loadEntries(true);
});

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 1500);
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

loadMeta();
loadEntries(true);
