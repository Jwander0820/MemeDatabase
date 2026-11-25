"use strict";
// 捲到底自動載入：監看列表下方的哨兵元素，進入視窗（含預載距離）時呼叫 onLoad。
// 一次載入後若內容仍不足以填滿畫面（哨兵還看得到），會接著再載一頁。
// canLoad 回傳 false 時不載入（載入中、已全部載完、或上次失敗等待使用者按重試），避免失敗時無限重試。
// 公開頁與管理頁共用；不支援 IntersectionObserver 的環境回傳 null，呼叫端保留「載入更多」按鈕即可。
(() => {
  function create(sentinel, {onLoad, canLoad, rootMargin = "800px"}) {
    if (!sentinel || typeof IntersectionObserver !== "function") return null;
    let visible = false;
    let running = false;
    const run = async () => {
      if (running || !visible || !canLoad()) return;
      running = true;
      try {
        await onLoad();
      } finally {
        running = false;
      }
      requestAnimationFrame(run);
    };
    const observer = new IntersectionObserver(entries => {
      visible = entries.some(entry => entry.isIntersecting);
      run();
    }, {rootMargin: `0px 0px ${rootMargin} 0px`});
    observer.observe(sentinel);
    // check：重新搜尋或重試成功後呼叫，若哨兵仍在視窗內就繼續載入。
    return {check: run, disconnect: () => observer.disconnect()};
  }
  window.MemeInfiniteScroll = {create};
})();
