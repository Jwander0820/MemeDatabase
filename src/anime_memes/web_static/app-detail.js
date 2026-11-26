async function openEntry(key) {
  const requestId = ++state.detailRequest;
  try {
    const response = await fetch(`/api/entries/${encodeURIComponent(key)}`);
    if (requestId !== state.detailRequest) return;
    if (!response.ok) {
      closeDetail();
      await loadEntries(true);
      return;
    }
    const item = await response.json();
    if (requestId !== state.detailRequest) return;
    renderDetail(item);
  } catch {
    if (requestId === state.detailRequest) showToast("詳情載入失敗，請重試");
  }
}

// 取得單筆最新資料（含待處理候選標籤），失敗時丟出例外。
async function fetchEntry(key) {
  const response = await fetch(`/api/entries/${encodeURIComponent(key)}`);
  if (!response.ok) throw new Error("entry request failed");
  return response.json();
}

function renderDetail(item) {
  state.selected = item;
  favoriteButton.textContent = item.favorite ? "\u2605" : "\u2606";
  favoriteButton.setAttribute("aria-pressed", String(Boolean(item.favorite)));
  drawerBody.innerHTML = detailTemplate(item);
  // 切換或重繪後，右欄捲回頂端。
  const detailPanel = drawerBody.querySelector?.(".detail-panel");
  if (detailPanel) detailPanel.scrollTop = 0;
  // 標籤欄位換成膠囊輸入（測試環境沒有 MemeTagInput 時維持純文字框）。
  globalThis.MemeTagInput?.mount(drawerBody.querySelector('#editForm [name="tags"]'));
  const wasOpen = Boolean(drawer.classList.contains?.("open"));
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  drawer.setAttribute("aria-modal", "true");
  if (!wasOpen) {
    // 儲存後重新載入同一筆時不搶焦點；只有第一次開啟才把焦點移進抽屜。
    focusBeforeDrawer = document.activeElement;
    closeDrawer.focus?.();
  }
  bindDetailForm(item);
  bindPublication(drawerBody.querySelector("#entryPublication"), item.key);
  markCurrentCard(true);
  updateDrawerNav();
}

// 表單目前的「名稱→值」快照；tag-input 的實際欄位是同名的隱藏 input，值已是逗號串。
function formValues() {
  const form = document.querySelector("#editForm");
  const values = {};
  for (const element of Array.from(form?.elements || [])) {
    if (element.name) values[element.name] = element.type === "checkbox" ? String(element.checked) : String(element.value);
  }
  const typing = form?.querySelector?.(".tag-input-field");
  if (typing) values.pendingTag = typing.value;
  return values;
}

function hasUnsavedChanges() {
  if (!state.formSnapshot) return false;
  const now = formValues();
  const before = state.formSnapshot;
  const names = new Set([...Object.keys(before), ...Object.keys(now)]);
  return [...names].some((name) => before[name] !== now[name]);
}

// 標示目前開啟的卡片（關閉時移除）；scroll 為 true 時捲到可見範圍。
function markCurrentCard(scroll = false) {
  const key = state.selected?.key;
  let current = null;
  document.querySelectorAll(".card").forEach((cardNode) => {
    const isCurrent = key !== undefined && cardNode.dataset?.key === key;
    cardNode.classList.toggle("is-current", isCurrent);
    if (isCurrent) current = cardNode;
  });
  if (scroll) current?.scrollIntoView?.({block: "nearest"});
}

function currentIndex() {
  const key = state.selected?.key;
  return key === undefined ? -1 : state.items.findIndex((entry) => entry.key === key);
}

function updateDrawerNav() {
  const index = currentIndex();
  const hasMore = state.items.length < state.total;
  prevEntry.disabled = index <= 0 || state.stepping;
  nextEntry.disabled = index < 0 || (index >= state.items.length - 1 && !hasMore) || state.stepping;
  entryPosition.textContent = index < 0 ? "" : `${index + 1} / ${state.total.toLocaleString("en-US")}`;
}

async function stepEntry(delta) {
  if (!state.selected || state.stepping) return;
  const index = currentIndex();
  if (index < 0) return;
  const target = index + delta;
  if (target < 0) return;
  if (target >= state.items.length && state.items.length >= state.total) return;
  if (hasUnsavedChanges() && !confirm("目前的修改尚未儲存，確定要切換嗎？")) return;
  const fromKey = state.selected.key;
  state.stepping = true;
  updateDrawerNav();
  try {
    if (target >= state.items.length) await loadEntries(false);
    // 載入期間若抽屜已關閉或換了條目，就不再跳轉。
    if (state.selected?.key !== fromKey) return;
    const next = state.items[currentIndex() + delta];
    if (next) await openEntry(next.key);
  } finally {
    state.stepping = false;
    updateDrawerNav();
  }
}

function isEditableTarget(target) {
  if (!target) return false;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return true;
  return Boolean(target.closest?.('input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="listbox"], [role="combobox"]'));
}

prevEntry.addEventListener("click", () => stepEntry(-1));
nextEntry.addEventListener("click", () => stepEntry(1));
globalThis.addEventListener?.("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  if (!state.selected || event.ctrlKey || event.altKey || event.metaKey || event.defaultPrevented) return;
  if (isEditableTarget(event.target) || document.querySelector("dialog[open]")) return;
  event.preventDefault();
  stepEntry(event.key === "ArrowLeft" ? -1 : 1);
});

function detailTemplate(item) {
  const image = item.image_url || item.thumbnail_url;
  const suggestions = suggestionTemplate(item.tag_suggestions || []);
  const statusOptions = ["ready", "needs_review", "ignored"].map((value) =>
    `<option value="${value}" ${item.status === value ? "selected" : ""}>${STATUS_LABELS[value]}</option>`).join("");
  const size = item.image_width && item.image_height ? `${item.image_width} × ${item.image_height}` : "";
  const meta = [
    ["Key", `<code>${escapeHtml(item.key)}</code>`],
    [T.sourceTime, escapeHtml(item.source_timestamp || "")],
    [T.file, escapeHtml(item.file_name || "")],
    [T.link, item.attachment_url ? `<a href="${attr(item.attachment_url)}" target="_blank" rel="noreferrer">${T.openOriginal}</a>` : ""],
    [T.download, escapeHtml(item.download_status || "")],
    [T.size, escapeHtml(size)],
    ["OCR", escapeHtml(item.ocr_status || "")],
  ].filter(([, value]) => value).map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`).join("");
  return `
    <section class="detail-media">
    <figure class="drawer-stage">
      ${image ? `<img class="drawer-image" src="${attr(image)}" alt="">` : `<div class="missing-image">${T.noPreview}</div>`}
    </figure>
    <div class="card-tags" id="detailTags">${item.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
    <!-- 公開設定放在左欄圖片下方：右欄只留審核與編輯，固定的儲存列才能一直留在畫面底部。 -->
    <form id="entryPublication" class="panel">
      <fieldset disabled>
        <legend>公開設定</legend>
        <label><input type="checkbox" name="selected"> 納入公開館藏</label>
        <label class="field">公開原圖網址（選填，留空則使用 R2 的 720P）<input name="original" type="url" placeholder="https://"></label>
        <button type="submit">儲存公開設定</button>
      </fieldset>
      <output role="status">正在讀取公開設定…</output>
    </form>
    <details class="meta-box panel">
      <summary>檔案與來源資訊</summary>
      <dl class="meta-list">
        ${meta}
      </dl>
      ${item.ocr_text ? `<p class="ocr-text"><strong>${T.ocrText}</strong><br>${escapeHtml(item.ocr_text)}</p>` : ""}
    </details>
    </section>
    <section class="detail-panel">
    ${suggestions}
    <form class="edit-form panel" id="editForm">
      <h3>基本資料</h3>
      <div class="field-grid">
        <label class="field"><span>${T.title}</span><input name="anime_title" value="${attr(item.anime_title || "")}"></label>
        <label class="field"><span>${T.speaker}</span><input name="speaker" value="${attr(item.speaker || "")}"></label>
      </div>
      <label class="field"><span>${T.quote}</span><textarea name="quote_text">${escapeHtml(item.quote_text || "")}</textarea></label>
      <label class="field"><span>梗圖名稱（選填）</span><input name="display_title" value="${attr(item.display_title || "")}"></label>
      <label class="field"><span>也可以這樣搜（選填，每行一個替代詞）</span><textarea name="search_aliases">${escapeHtml((item.search_aliases || []).join("\n"))}</textarea></label>
      <div class="field-grid">
        <label class="field"><span>${T.tags}</span><input name="tags" value="${attr((item.tags || []).join(", "))}"></label>
        <label class="field"><span>${T.status}</span><select name="status">${statusOptions}</select></label>
      </div>
      <label class="field"><span>圖片出處（選填）</span><input type="url" name="source_page" value="${attr(item.source_page || "")}"></label>
      <label class="field"><span>${T.notes}</span><textarea name="notes">${escapeHtml(item.notes || "")}</textarea></label>
      <div class="edit-actions">
        <button type="button" id="deleteEntry" class="danger">${T.deleteEntry}</button>
        <span class="spacer"></span>
        <button type="button" id="copyQuote">${T.copyQuote}</button>
        <button class="primary" type="submit" title="Ctrl+Enter 或 Ctrl+S" aria-keyshortcuts="Control+Enter Control+S">${T.save}</button>
      </div>
    </form>
    </section>
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
  state.formSnapshot = formValues();
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
        // 只更新這一筆與數量徽章；列表順序保持不變，左右鍵可繼續切換。
        const [fresh] = await Promise.all([fetchEntry(item.key), loadMeta()]);
        syncEntry(fresh);
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
    // 列表不重載：從已載入的順序移除，抽屜開著就改開原位置的下一張（沒有就上一張）。
    const index = state.items.findIndex((entry) => entry.key === item.key);
    const wasSelected = state.selected?.key === item.key;
    if (index >= 0) {
      state.items.splice(index, 1);
      state.total = Math.max(0, state.total - 1);
    }
    state.processed.delete(item.key);
    renderGrid({keepScroll: true});
    showToast(T.deleted);
    if (wasSelected) {
      const next = state.items[index] || state.items[index - 1];
      if (next) await openEntry(next.key);
      else closeDetail();
    }
    try {
      await loadMeta();
    } catch {
      showToast(T.decisionRefreshFailed);
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.dataset.saving) return;
    const payload = {
      display_title: form.display_title.value,
      search_aliases: form.search_aliases.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
      tags: form.tags.tagInput ? form.tags.tagInput.commit() : form.tags.value,
      anime_title: form.anime_title.value,
      source_page: form.source_page.value,
      speaker: form.speaker.value,
      quote_text: form.quote_text.value,
      status: form.status.value,
      notes: form.notes.value,
    };
    const submitted = formValues();
    const stillOpen = () => document.querySelector("#editForm") === form && state.selected?.key === item.key;
    form.dataset.saving = "true";
    try {
      const saved = await fetch(`/api/entries/${encodeURIComponent(item.key)}`, {
        method: "PATCH",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      if (!saved.ok) return showToast(errorMessage(await saved.json().catch(() => null), T.saveFailed));
      if (stillOpen()) state.formSnapshot = submitted;
      showToast(T.saved);
      globalThis.MemeTagInput?.refresh();
      try {
        const fresh = await fetchEntry(item.key);
        syncEntry(fresh);
        if (stillOpen() && !hasUnsavedChanges()) renderDetail(fresh);
        await loadMeta();
      } catch {
        showToast(T.decisionRefreshFailed);
      }
    } catch {
      showToast(T.saveFailed);
    } finally {
      delete form.dataset.saving;
    }
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
      favoriteButton.setAttribute("aria-pressed", String(Boolean(result.favorite)));
    }
    // 只更新列表中的同一張卡片，不重載列表。
    syncEntry({key: item.key, favorite: result.favorite});
  } catch {
    showToast("收藏更新失敗，請重試");
  } finally {
    favoriteButton.disabled = false;
  }
});

// 抽屜開啟時把 Tab 焦點圈在抽屜內；可聚焦清單在按鍵當下即時計算。
const FOCUSABLE = 'a[href], button, input, select, textarea, summary, [tabindex]';

function drawerFocusables() {
  return Array.from(drawer.querySelectorAll?.(FOCUSABLE) || []).filter((element) =>
    !element.disabled && element.tabIndex >= 0 && !element.closest?.("[hidden]") && element.getClientRects?.().length > 0);
}

globalThis.addEventListener?.("keydown", (event) => {
  if (event.key !== "Tab" || !state.selected || event.defaultPrevented || document.querySelector("dialog[open]")) return;
  const focusables = drawerFocusables();
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = document.activeElement;
  const inside = drawer.contains?.(active);
  if (event.shiftKey && (active === first || !inside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !inside)) {
    event.preventDefault();
    first.focus();
  }
});

function requestCloseDetail() {
  if (hasUnsavedChanges() && !confirm("目前的修改尚未儲存，確定要關閉嗎？")) return;
  closeDetail();
}

closeDrawer.addEventListener("click", requestCloseDetail);
scrim.addEventListener("click", requestCloseDetail);
globalThis.addEventListener?.("keydown", (event) => {
  if (event.key !== "Escape" || !state.selected || document.querySelector("dialog[open]")) return;
  requestCloseDetail();
});

function closeDetail() {
  // 讓尚未回來的開啟請求作廢，避免關閉後抽屜又自己彈開。
  state.detailRequest += 1;
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  drawer.setAttribute("aria-modal", "false");
  state.selected = null;
  state.formSnapshot = null;
  markCurrentCard();
  updateDrawerNav();
  favoriteButton.textContent = "\u2606";
  favoriteButton.setAttribute("aria-pressed", "false");
  drawerBody.innerHTML = "";
  const previous = focusBeforeDrawer;
  focusBeforeDrawer = null;
  if (previous?.isConnected) previous.focus?.();
  // 抽屜開著時自動載入暫停；關閉後若列表底部仍在視窗內就接著載。
  autoLoader?.check();
}
