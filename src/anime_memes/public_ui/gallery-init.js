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
byId("query").oninput = () => {
  ++listRequest;
  clearTimeout(timer);
  more.hidden = true;
  timer = setTimeout(() => search(true), 300);
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
renderScope();
search(true);
showRoute();
