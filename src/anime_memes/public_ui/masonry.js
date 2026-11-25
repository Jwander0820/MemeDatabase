"use strict";
// 由左到右的瀑布流：每張卡片放進目前最矮的欄，順序接近「由左到右、由上到下」。
// 卡片圖片需帶 width／height 屬性，讓高度在圖片載入前就確定，否則排列只能依已載入的高度估算。
(() => {
  function create(container, {minWidth = 210, mobileMinWidth = 160, breakpoint = 820} = {}) {
    let items = [];
    let columns = [];

    function columnCount() {
      const style = getComputedStyle(container);
      const gap = parseFloat(style.columnGap) || 16;
      const min = matchMedia(`(max-width: ${breakpoint}px)`).matches ? mobileMinWidth : minWidth;
      return Math.max(1, Math.floor((container.clientWidth + gap) / (min + gap)));
    }

    function build() {
      columns = Array.from({length: columnCount()}, () => {
        const column = document.createElement("div");
        column.className = "masonry-column";
        return column;
      });
      container.replaceChildren(...columns);
    }

    function place(node) {
      // 高度相同時取最左邊的欄，第一列因此依序由左到右填滿。
      let target = columns[0];
      for (const column of columns) {
        if (column.offsetHeight < target.offsetHeight) target = column;
      }
      target.append(node);
    }

    const api = {
      reset() {
        items = [];
        build();
      },
      append(nodes) {
        if (!columns.length || !columns[0].isConnected) build();
        for (const node of nodes) {
          items.push(node);
          place(node);
        }
      },
      // 原地替換一張卡片，不重排其他卡片；找不到舊節點時回傳 false。
      replace(oldNode, newNode) {
        const index = items.indexOf(oldNode);
        if (index < 0) return false;
        items[index] = newNode;
        oldNode.replaceWith(newNode);
        return true;
      },
      // 視窗寬度改變導致欄數不同時，依原本順序重新分配。
      relayout() {
        if (!items.length || columnCount() === columns.length) return;
        build();
        items.forEach(place);
      },
      get size() {
        return items.length;
      },
      get items() {
        return items;
      },
    };

    // 監看容器寬度（不只視窗縮放，側欄或版面變化也算）；欄數不變時 relayout 直接返回，不會反覆觸發。
    if (typeof ResizeObserver === "function") {
      let frame = 0;
      new ResizeObserver(() => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => api.relayout());
      }).observe(container);
    }
    return api;
  }

  window.MemeMasonry = {create};
})();
