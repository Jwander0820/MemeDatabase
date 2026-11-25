// ---------- 多選與批次公開 ----------
function isPicked(key) {
  return state.pickAll || state.picked.has(key);
}

function setSelecting(on) {
  state.selecting = on;
  state.picked.clear();
  state.pickAll = false;
  state.lastPicked = -1;
  selectMode.textContent = on ? "完成" : "多選";
  selectMode.setAttribute("aria-pressed", String(on));
  bulkBar.hidden = !on;
  if (on) {
    grid.classList.add("selecting");
    if (state.selected) closeDetail();
  } else {
    grid.classList.remove("selecting");
  }
  renderGrid();
}

function togglePick(item, range) {
  // 從「全選篩選結果」改為手動調整時，先展開成目前已載入的卡片。
  if (state.pickAll) {
    state.pickAll = false;
    state.items.forEach((entry) => state.picked.add(entry.key));
  }
  const index = state.items.findIndex((entry) => entry.key === item.key);
  const on = !state.picked.has(item.key);
  // Shift + 點擊：選取上一次點選到這一張之間的範圍。
  const hasAnchor = range && state.lastPicked >= 0;
  const from = hasAnchor ? Math.min(state.lastPicked, index) : index;
  const to = hasAnchor ? Math.max(state.lastPicked, index) : index;
  for (let i = from; i <= to; i += 1) {
    const key = state.items[i].key;
    if (on) state.picked.add(key);
    else state.picked.delete(key);
  }
  state.lastPicked = index;
  renderPicks();
}

function renderPicks() {
  // 瀑布流會打亂 DOM 順序，改用卡片上的 key 對應。
  grid.querySelectorAll(".card").forEach((node) => {
    const picked = isPicked(node.dataset.key);
    node.classList.toggle("picked", picked);
    node.setAttribute("aria-pressed", String(picked));
  });
  updateBulkBar();
}

function updateBulkBar() {
  if (!state.selecting) return;
  const count = state.pickAll ? state.total : state.picked.size;
  bulkCount.textContent = state.pickAll ? `已選目前篩選結果全部 ${count} 張` : `已選 ${count} 張`;
  bulkPublish.disabled = bulkUnpublish.disabled = bulkClear.disabled = count === 0;
}

function currentFilter() {
  const search = params(1);
  const filter = {};
  ["status", "q", "field", "tag", "favorite", "missing_image", "no_tags", "published"].forEach((name) => {
    if (search.has(name)) filter[name] = search.get(name);
  });
  return filter;
}

async function bulkSetPublished(selected) {
  const count = state.pickAll ? state.total : state.picked.size;
  if (!count) return;
  const verb = selected ? "設為公開" : "取消公開";
  if ((state.pickAll || count > 20) && !confirm(`確定將 ${count} 張${verb}？`)) return;
  const body = state.pickAll
    ? {publish_selected: selected, filter: currentFilter()}
    : {publish_selected: selected, keys: [...state.picked]};
  bulkPublish.disabled = bulkUnpublish.disabled = true;
  try {
    const response = await fetch("/api/publication/bulk", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "批次更新失敗，請重試");
    const skipped = data.skipped_videos?.length ? `，略過影片 ${data.skipped_videos.length} 張` : "";
    showToast(`已${verb} ${data.matched} 張（實際變更 ${data.changed} 張${skipped}）`);
    if (typeof invalidatePublication === "function") invalidatePublication();
    state.picked.clear();
    state.pickAll = false;
    await loadEntries(true);
  } catch (error) {
    showToast(error.message);
  } finally {
    updateBulkBar();
  }
}

selectMode.addEventListener("click", () => setSelecting(!state.selecting));
bulkSelectAll.addEventListener("click", () => {
  state.pickAll = true;
  state.picked.clear();
  renderPicks();
});
bulkClear.addEventListener("click", () => {
  state.pickAll = false;
  state.picked.clear();
  renderPicks();
});
bulkPublish.addEventListener("click", () => bulkSetPublished(true));
bulkUnpublish.addEventListener("click", () => bulkSetPublished(false));
globalThis.addEventListener?.("keydown", (event) => {
  if (event.key === "Escape" && state.selecting && !document.querySelector("dialog[open]")) setSelecting(false);
});

