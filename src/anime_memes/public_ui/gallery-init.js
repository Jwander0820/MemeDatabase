"use strict";
// 首頁內切換，避免連點品牌時整頁重載與重複抓取圖片。
byId("home").onclick = event => {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  byId("publicCollection").onclick?.();
  if (location.hash) {
    history.replaceState(null, "", location.pathname + location.search);
    if (dialog.open) dialog.close();
  }
  // 回首頁時網址也回到沒有條件的樣子（會新增一筆歷史，按上一頁可回到剛才的搜尋）。
  if (location.search) {
    history.pushState(null, "", location.pathname);
    typingEntry = false;
  }
  const needsSearch = !isHomeMode();
  byId("query").value = "";
  byId("sort").value = "";
  activeTag = "";
  favoritesOnly = false;
  activeScope = "";
  renderScope();
  renderTags();
  if (needsSearch) search(true);
  window.scrollTo?.({top: 0, behavior: "instant"});
};
let rollNumber = 0;
let diceFace = 5;
byId("rollMemes").onclick = async () => {
  const button = byId("rollMemes");
  if (button.disabled) return;
  button.disabled = true;
  // 從其餘五個點數抽選，確保每按一次都看得到變化。
  diceFace = (diceFace + Math.floor(Math.random() * 5)) % 6 + 1;
  byId("rollDiceFace").setAttribute("href", `#i-dice-${diceFace}`);
  byId("query").value = "";
  byId("sort").value = "";
  activeTag = "";
  favoritesOnly = false;
  activeScope = "";
  renderScope();
  renderTags();
  // 32 字元內的種子；每次點擊不同，分頁則沿用同一種子。
  const seed = `roll-${Date.now().toString(36)}-${(++rollNumber).toString(36)}`;
  try { await search(true, seed); }
  finally { button.disabled = false; }
};
byId("filters").onsubmit = event => { event.preventDefault(); search(true); };
// 注音、倉頡等輸入法組字期間不搜尋（否則會拿「ㄅㄨˋ」這類半成品去查），選字完成才送出。
let composing = false;
function scheduleSearch() {
  ++listRequest;
  clearTimeout(timer);
  more.hidden = true;
  timer = setTimeout(() => {
    urlMode = "typing";
    search(true);
  }, 300);
}
// 組字事件沒有 oncomposition* 屬性可用，必須用 addEventListener。
byId("query").addEventListener("compositionstart", () => {
  composing = true;
  clearTimeout(timer);
});
byId("query").addEventListener("compositionend", () => {
  composing = false;
  scheduleSearch();
});
byId("query").oninput = event => {
  if (composing || event?.isComposing) return;
  scheduleSearch();
};
// 排序改變時，「最近新增」chip 要跟著同步。
byId("sort").onchange = () => { renderTags(); search(true); };
more.onclick = () => search(retryFromStart);
// 捲到列表底部自動載入下一頁；不支援 IntersectionObserver 時 scroller 為 null，沿用按鈕。
scroller = window.MemeInfiniteScroll?.create(byId("sentinel"), {
  rootMargin: "0px",
  canLoad: canLoadMore,
  onLoad: () => search(false),
});
// 詳情彈窗開啟時不載入；關閉後哨兵若仍在視窗內就補載。
dialog.addEventListener("close", () => scroller?.check());
// 上一頁／下一頁：搜尋條件不同才重查；只有 #/m/id 變動（開關詳情）時交給 hashchange 處理。
window.addEventListener("popstate", () => {
  if (!readUrl()) return;
  clearTimeout(timer);
  renderScope();
  renderTags();
  urlMode = "none";
  typingEntry = false;
  search(true);
});
readUrl();
renderScope();
search(true);
showRoute();
