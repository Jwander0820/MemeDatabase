"use strict";
byId("filters").onsubmit = event => { event.preventDefault(); search(true); };
byId("query").oninput = () => {
  ++listRequest;
  clearTimeout(timer);
  more.hidden = true;
  timer = setTimeout(() => search(true), 300);
};
byId("sort").onchange = () => search(true);
more.onclick = () => search(retryFromStart);
// 捲到列表底部自動載入下一頁；不支援 IntersectionObserver 時 scroller 為 null，沿用按鈕。
scroller = window.MemeInfiniteScroll?.create(byId("sentinel"), {
  canLoad: canLoadMore,
  onLoad: () => search(false),
});
// 詳情彈窗開啟時不載入；關閉後哨兵若仍在視窗內就補載。
dialog.addEventListener("close", () => scroller?.check());
renderScope();
search(true);
showRoute();
