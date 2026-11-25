async function openEntry(key) {
  const response = await fetch(`/api/entries/${encodeURIComponent(key)}`);
  if (!response.ok) {
    closeDetail();
    await loadEntries(true);
    return;
  }
  const item = await response.json();
  state.selected = item;
  favoriteButton.textContent = item.favorite ? "\u2605" : "\u2606";
  favoriteButton.setAttribute("aria-pressed", String(Boolean(item.favorite)));
  drawerBody.innerHTML = detailTemplate(item);
  // 標籤欄位換成膠囊輸入（測試環境沒有 MemeTagInput 時維持純文字框）。
  globalThis.MemeTagInput?.mount(drawerBody.querySelector('#editForm [name="tags"]'));
  const wasOpen = Boolean(drawer.classList.contains?.("open"));
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  if (!wasOpen) {
    // 儲存後重新載入同一筆時不搶焦點；只有第一次開啟才把焦點移進抽屜。
    focusBeforeDrawer = document.activeElement;
    closeDrawer.focus?.();
  }
  bindDetailForm(item);
  bindPublication(drawerBody.querySelector("#entryPublication"), item.key);
}

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
    <figure class="drawer-stage">
      ${image ? `<img class="drawer-image" src="${attr(image)}" alt="">` : `<div class="missing-image">${T.noPreview}</div>`}
    </figure>
    <div class="card-tags" id="detailTags">${item.tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
    ${suggestions}
    <form class="edit-form panel" id="editForm">
      <h3>基本資料</h3>
      <div class="field-grid">
        <label class="field"><span>${T.title}</span><input name="anime_title" value="${attr(item.anime_title || "")}"></label>
        <label class="field"><span>${T.speaker}</span><input name="speaker" value="${attr(item.speaker || "")}"></label>
      </div>
      <label class="field"><span>${T.quote}</span><textarea name="quote_text">${escapeHtml(item.quote_text || "")}</textarea></label>
      <label class="field"><span>圖片出處（選填）</span><input type="url" name="source_page" value="${attr(item.source_page || "")}"></label>
      <div class="field-grid">
        <label class="field"><span>${T.manualTags}</span><input name="tags" value="${attr((item.manual_tags || []).join(", "))}"></label>
        <label class="field"><span>${T.status}</span><select name="status">${statusOptions}</select></label>
      </div>
      <label class="field"><span>${T.notes}</span><textarea name="notes">${escapeHtml(item.notes || "")}</textarea></label>
      <div class="edit-actions">
        <button type="button" id="deleteEntry" class="danger">${T.deleteEntry}</button>
        <span class="spacer"></span>
        <button type="button" id="copyQuote">${T.copyQuote}</button>
        <button class="primary" type="submit">${T.save}</button>
      </div>
    </form>
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
        await Promise.all([loadMeta(), loadEntries(true)]);
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
    closeDetail();
    await Promise.all([loadMeta(), loadEntries(true)]);
    showToast(T.deleted);
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      anime_title: form.anime_title.value,
      source_page: form.source_page.value,
      speaker: form.speaker.value,
      quote_text: form.quote_text.value,
      status: form.status.value,
      notes: form.notes.value,
    };
    const saved = await fetch(`/api/entries/${encodeURIComponent(item.key)}`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    if (!saved.ok) return showToast(T.saveFailed);
    const tags = await fetch(`/api/entries/${encodeURIComponent(item.key)}/tags`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({tags: form.tags.tagInput ? form.tags.tagInput.commit() : form.tags.value}),
    });
    if (!tags.ok) return showToast(T.tagsFailed);
    showToast(T.saved);
    globalThis.MemeTagInput?.refresh();
    await loadMeta();
    await loadEntries(true);
    await openEntry(item.key);
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
    await loadEntries(true);
  } catch {
    showToast("收藏更新失敗，請重試");
  } finally {
    favoriteButton.disabled = false;
  }
});

closeDrawer.addEventListener("click", closeDetail);
scrim.addEventListener("click", closeDetail);
globalThis.addEventListener?.("keydown", (event) => {
  if (event.key !== "Escape" || !state.selected || document.querySelector("dialog[open]")) return;
  closeDetail();
});

function closeDetail() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  state.selected = null;
  favoriteButton.textContent = "\u2606";
  favoriteButton.setAttribute("aria-pressed", "false");
  drawerBody.innerHTML = "";
  const previous = focusBeforeDrawer;
  focusBeforeDrawer = null;
  if (previous?.isConnected) previous.focus?.();
  // 抽屜開著時自動載入暫停；關閉後若列表底部仍在視窗內就接著載。
  autoLoader?.check();
}
