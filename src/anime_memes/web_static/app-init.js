loadMore.addEventListener("click", () => {
  loadEntries(false);
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
      loadEntries(true);
    });
});

clearTag.addEventListener("click", () => {
  state.tag = "";
  loadMeta();
  loadEntries(true);
});
loadMeta();
loadEntries(true);
