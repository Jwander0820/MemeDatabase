openCreate.addEventListener("click", () => {
  // 開啟時一律重設：上一次的 close 事件可能還沒送達，不能讓新草稿沿用舊內容或舊的送出鎖。
  clearCreateForm();
  createFormActive = true;
  createDialog.showModal();
});

[closeCreate, cancelCreate].forEach((button) => {
  button.addEventListener("click", () => createDialog.close());
});

createImage.addEventListener("change", () => {
  if (createPreviewUrl) URL.revokeObjectURL(createPreviewUrl);
  const file = createImage.files[0];
  if (!file) {
    createPreview.removeAttribute("src");
    createPreview.closest(".upload-field").classList.remove("has-preview");
    return;
  }
  createPreviewUrl = URL.createObjectURL(file);
  createPreview.src = createPreviewUrl;
  createPreview.closest(".upload-field").classList.add("has-preview");
  createError.textContent = "";
});

function selectTransferredImage(files) {
  const [file] = files;
  if (files.length !== 1 || !createImage.accept.split(",").includes(file.type)) {
    createError.textContent = "請一次加入一張 JPG、PNG、WebP 或 GIF 圖片。";
    return;
  }
  const transfer = new DataTransfer();
  transfer.items.add(file);
  createImage.files = transfer.files;
  createImage.dispatchEvent(new Event("change"));
}

createDialog.addEventListener("dragover", (event) => {
  if (!event.dataTransfer.types.includes("Files")) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
});
createDialog.addEventListener("drop", (event) => {
  if (!event.dataTransfer.files.length) return;
  event.preventDefault();
  selectTransferredImage(event.dataTransfer.files);
});
createDialog.addEventListener("paste", (event) => {
  const files = event.clipboardData?.files;
  if (!files?.length) return;
  event.preventDefault();
  selectTransferredImage(files);
});

createDialog.addEventListener("close", resetCreateForm);

// 點框外（backdrop）關閉：按下與放開都在框外才算，避免框內拖曳選字時誤關。
// 以座標判斷是否在框外，點到框內捲軸時不會誤關。
function closeOnBackdrop(dialog, canClose = () => true) {
  const outsideBox = (event) => {
    if (event.target !== dialog) return false;
    const box = dialog.getBoundingClientRect();
    return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
  };
  let pressedOutside = false;
  dialog.addEventListener("pointerdown", (event) => { pressedOutside = outsideBox(event); });
  dialog.addEventListener("click", (event) => {
    if (pressedOutside && outsideBox(event) && canClose()) dialog.close();
    pressedOutside = false;
  });
}

// 新增表單已選圖或已輸入內容時不因點框外而關閉，避免草稿遺失；仍可用取消或 × 關閉。
function createFormIsBlank() {
  if (createImage.files.length) return false;
  const typingTag = createForm.querySelector(".tag-input-field")?.value.trim();
  return !typingTag && ["anime_title", "speaker", "quote_text", "display_title", "search_aliases", "source_page", "tags", "notes"].every((name) => !createForm.elements[name].value.trim());
}

closeOnBackdrop(createDialog, createFormIsBlank);
if (globalThis.MemeTagInput) globalThis.MemeTagInput.mount(createForm.elements.tags);
closeOnBackdrop(document.querySelector("#publicationDialog"));

// 每次開啟或關閉新增視窗都換一個編號；送出時記下編號，回應回來時編號不同代表視窗已屬於別的草稿。
let createSession = 0;
// 目前這次開啟的表單是否尚未重設；確保每次開啟只重設一次。
let createFormActive = false;

// 顯示頁面層級提示（不在對話框內）；action 為可選的操作按鈕，提示會停留較久。
function showCreateNotice(message, action = null) {
  showToast(message);
  if (!action) return;
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = action.label;
  button.style.pointerEvents = "auto";
  button.style.marginLeft = "12px";
  button.addEventListener("click", action.run);
  toast.appendChild(button);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 30000);
}

// 只重新讀取統計、清單與新增的那筆；絕不重新 POST。任何一步失敗都會丟出例外。
async function refreshAfterCreate(key, showDetail) {
  if (await loadMeta() === false) throw new Error("meta refresh failed");
  await loadEntries(true);
  if (state.loadFailed) throw new Error("entries refresh failed");
  if (!showDetail()) return;
  const requestId = ++state.detailRequest;
  const item = await fetchEntry(key);
  if (requestId === state.detailRequest && showDetail()) renderDetail(item);
}

async function refreshCreatedEntry(key, showDetail, isRetry = false) {
  try {
    await refreshAfterCreate(key, showDetail);
    // 首次成功時「已新增」已顯示過；重試成功才需要蓋掉先前的失敗提示。
    if (isRetry) showCreateNotice(T.created);
  } catch (_error) {
    showCreateNotice(T.createdRefreshFailed, {label: T.retry, run: () => refreshCreatedEntry(key, showDetail, true)});
  }
}

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveCreate.disabled) return;
  const session = createSession;
  const owns = () => session === createSession;
  let created = false;
  createError.textContent = "";
  saveCreate.disabled = true;
  try {
    createForm.elements.tags.tagInput?.commit();
    const body = new FormData(createForm);
    const aliases = createForm.elements.search_aliases.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    body.set("search_aliases", JSON.stringify(aliases));
    let response;
    try {
      response = await fetch("/api/entries/manual", {
        method: "POST",
        body,
      });
    } catch (_error) {
      // 請求已送出但沒有回應：伺服器可能已建立，不能讓使用者直接重送。
      if (owns()) createError.textContent = T.createUnknown;
      else showCreateNotice(T.createUnknown);
      return;
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const reason = errorMessage(data, T.createFailed);
      if (owns()) createError.textContent = reason;
      else showCreateNotice(reason);
      return;
    }
    if (!data.key) {
      if (owns()) createError.textContent = T.createUnknown;
      else showCreateNotice(T.createUnknown);
      return;
    }
    created = true;
    showCreateNotice(T.created);
    let showDetail = () => false;
    if (owns()) {
      closeCreateDialog();
      const after = createSession;
      showDetail = () => after === createSession;
      searchInput.value = "";
      setSearchField("");
      statusFilter.value = data.status;
      sortFilter.value = "";
      favoriteOnly.checked = false;
      missingImage.checked = false;
      noTags.checked = false;
      publishedOnly.checked = false;
      unpublishedOnly.checked = false;
      state.tag = "";
    }
    await refreshCreatedEntry(data.key, showDetail);
  } catch (_error) {
    // 已收到 201 之後的錯誤只影響畫面，不能讓使用者以為要重新上傳。
    if (created) showCreateNotice(T.createdRefreshFailed);
    else if (owns()) createError.textContent = T.createFailed;
    else showCreateNotice(T.createFailed);
  } finally {
    if (owns()) saveCreate.disabled = false;
  }
});

function resetCreateForm() {
  // close 事件是非同步送達的：若送達時視窗已重新開啟給下一份草稿，就不能清空或換掉它的編號。
  if (createDialog.open || !createFormActive) return;
  createFormActive = false;
  clearCreateForm();
}

// 程式主動關閉時同步完成重設，之後才送達的 close 事件就不會再換一次編號。
function closeCreateDialog() {
  createDialog.close();
  resetCreateForm();
}

function clearCreateForm() {
  createSession += 1;
  saveCreate.disabled = false;
  createForm.reset();
  createForm.elements?.tags?.tagInput?.setValue("");
  createError.textContent = "";
  if (createPreviewUrl) URL.revokeObjectURL(createPreviewUrl);
  createPreviewUrl = "";
  createPreview.removeAttribute("src");
  createPreview.closest(".upload-field").classList.remove("has-preview");
}
