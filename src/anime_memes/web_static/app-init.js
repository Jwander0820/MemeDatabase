globalThis.addEventListener?.("keydown", (event) => {
  const saveKey = ["Enter", "s", "S"].includes(event.key);
  if (!saveKey || !event.ctrlKey || event.altKey || event.shiftKey || event.metaKey ||
      event.isComposing || event.keyCode === 229 || event.defaultPrevented) return;
  const dialog = document.querySelector("dialog[open]");
  let form;
  if (dialog === createDialog) form = createForm;
  else if (!dialog && state.selected && !event.target?.closest?.("#entryPublication")) {
    form = document.querySelector("#editForm");
  }
  if (!form) return;
  event.preventDefault();
  event.stopPropagation();
  if (!event.repeat) form.requestSubmit();
}, true);

loadMore.addEventListener("click", async () => {
  // 第一頁就失敗時重試要整份重載；否則接續載入下一頁。
  const reset = state.items.length === 0;
  state.loadFailed = false;
  await loadEntries(reset);
  if (!reset) autoLoader?.check();
});

// 「待審核」快速篩選＝狀態下拉的捷徑，兩者互相同步。
needsReviewOnly.addEventListener("change", () => {
  statusFilter.value = needsReviewOnly.checked ? "needs_review" : "ready";
  loadEntries(true);
});

// 待處理候選標籤可能在任何狀態的梗圖上，勾選時若還停在「可用」就改看「全部」。
pendingSuggestions.addEventListener("change", () => {
  if (pendingSuggestions.checked && statusFilter.value === "ready") {
    statusFilter.value = "all";
    syncNeedsReview();
  }
  loadEntries(true);
});

// 「已公開」「未公開」互斥：勾選其中一個時取消另一個。
[[publishedOnly, unpublishedOnly], [unpublishedOnly, publishedOnly]].forEach(([el, other]) => {
  el.addEventListener("change", () => { if (el.checked) other.checked = false; });
});

// 搜尋範圍：更新狀態、radio 勾選與 placeholder。
function setSearchField(value) {
  state.searchField = SEARCH_PLACEHOLDERS[value] !== undefined ? value : "";
  searchInput.placeholder = SEARCH_PLACEHOLDERS[state.searchField];
  searchScope.querySelectorAll?.('input[name="searchField"]').forEach((radio) => {
    radio.checked = radio.value === state.searchField;
  });
}

searchScope.addEventListener("change", (event) => {
  setSearchField(event.target.value);
  // 沒有搜尋字時 field 不會送出，不必重新載入。
  if (searchInput.value.trim()) loadEntries(true);
});

[searchInput, statusFilter, sortFilter, favoriteOnly, missingImage, noTags, publishedOnly, unpublishedOnly].forEach((el) => {
  el.addEventListener(el === searchInput ? "input" : "change", el === searchInput
    ? debounce(() => loadEntries(true), 180)
    : () => {
      if (el === sortFilter) state.randomSeed = String(Math.random());
      if (el === statusFilter) syncNeedsReview();
      loadEntries(true);
    });
});

// 清掉審核期間留在列表上的「已處理」卡片。
refreshList.addEventListener("click", () => loadEntries(true));

clearTag.addEventListener("click", () => {
  state.tag = "";
  loadMeta();
  loadEntries(true);
});
loadMeta();
loadEntries(true);
