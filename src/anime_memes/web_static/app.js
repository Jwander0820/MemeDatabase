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
  manualTags: "人工標籤",
  decisionRefreshFailed: "審核已完成，清單更新失敗，請重新整理",
};

const STATUS_LABELS = {ready: "可用", needs_review: "待審核", ignored: "已忽略"};

const SEARCH_PLACEHOLDERS = {
  "": "台詞、作品、角色、標籤",
  work: "搜尋作品名稱",
  speaker: "搜尋角色",
  quote: "搜尋台詞",
};
const searchScope = document.querySelector("#searchScope");

const state = {
  searchField: "",
  page: 1,
  pageSize: 48,
  total: 0,
  tag: "",
  items: [],
  selected: null,
  randomSeed: String(Math.random()),
  entriesRequest: 0,
  // 開啟詳情的請求序號；較舊的回應回來時丟棄。
  detailRequest: 0,
  // 表單初始值快照（切換上一張／下一張前比對是否有未儲存修改）與切換中旗標。
  formSnapshot: null,
  stepping: false,
  loading: false,
  // 上一次載入下一頁失敗：停止自動載入，等使用者按「重試」。
  loadFailed: false,
  // 多選模式：picked 為明確點選的 key；pickAll 代表「目前篩選結果全部」，交由後端依條件處理。
  selecting: false,
  picked: new Set(),
  pickAll: false,
  lastPicked: -1,
};

const grid = document.querySelector("#grid");
// 瀑布流排列；沒有 MemeMasonry（例如單元測試）時退回直接附加。
const masonry = globalThis.MemeMasonry?.create(grid);
const resultCount = document.querySelector("#resultCount");
const searchInput = document.querySelector("#searchInput");
const statusFilter = document.querySelector("#statusFilter");
const sortFilter = document.querySelector("#sortFilter");
const favoriteOnly = document.querySelector("#favoriteOnly");
const missingImage = document.querySelector("#missingImage");
const noTags = document.querySelector("#noTags");
const publishedOnly = document.querySelector("#publishedOnly");
const unpublishedOnly = document.querySelector("#unpublishedOnly");
const needsReviewOnly = document.querySelector("#needsReviewOnly");
const pendingSuggestions = document.querySelector("#pendingSuggestions");
const needsReviewCount = document.querySelector("#needsReviewCount");
const pendingSuggestionsCount = document.querySelector("#pendingSuggestionsCount");
const selectMode = document.querySelector("#selectMode");
const bulkBar = document.querySelector("#bulkBar");
const bulkCount = document.querySelector("#bulkCount");
const bulkSelectAll = document.querySelector("#bulkSelectAll");
const bulkClear = document.querySelector("#bulkClear");
const bulkPublish = document.querySelector("#bulkPublish");
const bulkUnpublish = document.querySelector("#bulkUnpublish");
const tagList = document.querySelector("#tagList");
const clearTag = document.querySelector("#clearTag");
const loadMore = document.querySelector("#loadMore");
const loadRow = document.querySelector(".load-row");
const loadEnd = document.querySelector("#loadEnd");
const drawer = document.querySelector("#drawer");
const drawerBody = document.querySelector("#drawerBody");
const closeDrawer = document.querySelector("#closeDrawer");
const favoriteButton = document.querySelector("#favoriteButton");
const prevEntry = document.querySelector("#prevEntry");
const nextEntry = document.querySelector("#nextEntry");
const entryPosition = document.querySelector("#entryPosition");
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
const scrim = document.querySelector("#scrim");
let createPreviewUrl = "";
// 抽屜開啟前的焦點位置，關閉時歸還，鍵盤使用者不必從頭 Tab。
let focusBeforeDrawer = null;

// 捲到底自動載入；不支援 IntersectionObserver（或沒有共用模組）時為 null，改用「載入更多」按鈕。
const autoLoader = globalThis.MemeInfiniteScroll?.create(loadRow, {
  canLoad: () => !state.loading && !state.loadFailed && state.items.length < state.total && !drawer.classList.contains?.("open"),
  onLoad: () => loadEntries(false),
}) ?? null;

// 快速篩選的「待審核」與狀態下拉同步：只有狀態為 needs_review 才勾選。
function syncNeedsReview() {
  needsReviewOnly.checked = statusFilter.value === "needs_review";
}

function params(page = state.page) {
  const search = new URLSearchParams();
  search.set("page", page);
  search.set("page_size", state.pageSize);
  search.set("status", statusFilter.value);
  search.set("sort", sortFilter.value);
  search.set("seed", state.randomSeed);
  if (searchInput.value.trim()) {
    search.set("q", searchInput.value.trim());
    if (state.searchField) search.set("field", state.searchField);
  }
  if (state.tag) search.set("tag", state.tag);
  if (favoriteOnly.checked) search.set("favorite", "1");
  if (missingImage.checked) search.set("missing_image", "1");
  if (noTags.checked) search.set("no_tags", "1");
  if (publishedOnly.checked !== unpublishedOnly.checked) search.set("published", publishedOnly.checked ? "1" : "0");
  if (pendingSuggestions.checked) search.set("pending_suggestions", "1");
  return search;
}

async function loadEntries(reset = false) {
  if (!reset && state.loading) return;
  const requestId = ++state.entriesRequest;
  state.loading = true;
  loadMore.disabled = true;
  if (reset) state.loadFailed = false;
  try {
    const response = await fetch(`/api/entries?${params(reset ? 1 : state.page + 1)}`);
    if (!response.ok) throw new Error("entries request failed");
    const data = await response.json();
    if (requestId !== state.entriesRequest) return;
    state.page = data.page;
    state.total = data.total;
    // 篩選條件改變後，「全選目前篩選結果」不再代表同一批圖。
    if (reset) state.pickAll = false;
    state.items = reset ? data.items : state.items.concat(data.items);
    state.loadFailed = false;
    renderGrid(reset ? {} : {added: data.items});
  } catch {
    if (requestId === state.entriesRequest) {
      state.loadFailed = true;
      showToast("清單載入失敗，請重試");
    }
  } finally {
    if (requestId === state.entriesRequest) {
      state.loading = false;
      loadMore.disabled = false;
      renderLoadControls();
      // 載入完成後若底部仍在視窗內，繼續載入（失敗時 canLoad 為 false，不會重試）。
      if (reset) autoLoader?.check();
    }
  }
}

// 數量徽章：0 時隱藏。
function setChipCount(node, count) {
  node.textContent = String(count);
  node.hidden = !count;
}

async function loadMeta() {
  const response = await fetch("/api/meta");
  if (!response.ok) return;
  const data = await response.json();
  const reviewCount = (data.statuses || []).find((row) => row.status === "needs_review")?.count || 0;
  setChipCount(needsReviewCount, reviewCount);
  setChipCount(pendingSuggestionsCount, data.pending_suggestions || 0);
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

// added：載入下一頁時只把新卡片接在後面。整份重畫會讓網格瞬間清空、頁面高度歸零，
//   瀏覽器因此把捲動位置拉回最上面（自動載入時尤其明顯）。
// keepScroll：不換資料、只需重畫卡片外觀時（例如切換多選模式）保留原本的捲動位置。
function renderGrid({added = null, keepScroll = false} = {}) {
  resultCount.textContent = state.total ? `已載入 ${state.items.length}／${state.total} 筆` : "0 筆";
  const appendable = added && state.total && (masonry ? masonry.size > 0 : grid.children.length > 0);
  if (appendable) {
    if (masonry) masonry.append(added.map(card));
    else added.forEach((item) => grid.appendChild(card(item)));
  } else {
    const scrollY = globalThis.scrollY || 0;
    grid.innerHTML = state.total ? "" : `
      <div class="empty">
        <div class="empty-mark" aria-hidden="true">?</div>
        <strong>沒有符合條件的梗圖</strong>
        <p>調整搜尋字詞、狀態或標籤篩選再試一次。</p>
      </div>`;
    if (state.total && masonry) {
      masonry.reset();
      masonry.append(state.items.map(card));
    } else {
      state.items.forEach((item) => grid.appendChild(card(item)));
    }
    if (keepScroll) globalThis.scrollTo?.(0, scrollY);
  }
  renderLoadControls();
  updateBulkBar();
  updateDrawerNav();
}

// 「載入更多」按鈕與「已經到底了」提示：有自動載入時按鈕只在失敗時以「重試」出現。
function renderLoadControls() {
  const done = state.items.length >= state.total && !state.loadFailed;
  loadMore.disabled = state.loading;
  loadMore.textContent = state.loadFailed ? "重試" : "載入更多";
  loadMore.hidden = done || (autoLoader !== null && !state.loadFailed);
  loadEnd.hidden = !(done && state.total > 0);
}

// 原圖寬高與縮圖等比例；寫成屬性讓瀑布流在圖片載入前就知道卡片高度。
function imageSize(item) {
  const width = Number(item.image_width);
  const height = Number(item.image_height);
  return width > 0 && height > 0 ? ` width="${width}" height="${height}"` : "";
}

// GIF 與影片在卡片上標示；影片不會公開。
function mediaKind(item) {
  const name = String(item.file_name || "").toLowerCase();
  if (name.endsWith(".gif")) return "GIF";
  return /\.(mp4|mov|webm|m4v)$/.test(name) ? "影片" : "";
}

function card(item) {
  const node = document.createElement("article");
  node.className = "card";
  node.dataset.key = item.key;
  node.tabIndex = 0;
  if (state.selected?.key === item.key) node.classList.add("is-current");
  const image = item.thumbnail_url || item.image_url;
  const tags = item.tags.slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("");
  const badges = [
    item.publish_selected ? `<span class="badge pub">公開</span>` : "",
    mediaKind(item) ? `<span class="badge kind">${mediaKind(item)}</span>` : "",
    item.favorite ? `<span class="badge fav" aria-label="\u5df2\u6536\u85cf">\u2605</span>` : "",
    item.status && item.status !== "ready" ? `<span class="badge status-${attr(item.status)}">${escapeHtml(STATUS_LABELS[item.status] || item.status)}</span>` : "",
  ].join("");
  node.innerHTML = `
    <div class="card-media">
      ${image ? `<img src="${attr(image)}" alt="" loading="lazy"${imageSize(item)}>` : `<div class="missing-image">${T.missingImage}</div>`}
      ${badges ? `<div class="card-badges">${badges}</div>` : ""}
    </div>
    <div class="card-body">
      <div class="card-title">${escapeHtml(item.anime_title || T.unknownTitle)}</div>
      <div class="card-speaker">${escapeHtml(item.speaker || T.unknownSpeaker)}</div>
      <p class="card-quote">${escapeHtml(item.quote_text || "")}</p>
      <div class="card-tags">${tags}</div>
    </div>
  `;
  if (state.selecting) {
    node.setAttribute("aria-pressed", String(isPicked(item.key)));
    if (isPicked(item.key)) node.classList.add("picked");
  }
  const activate = (event) => (state.selecting ? togglePick(item, event.shiftKey) : openEntry(item.key));
  node.addEventListener("click", activate);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(event);
    }
  });
  return node;
}
