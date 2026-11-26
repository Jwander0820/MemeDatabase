openCreate.addEventListener("click", () => {
  createError.textContent = "";
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

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveCreate.disabled) return;
  createError.textContent = "";
  saveCreate.disabled = true;
  try {
    createForm.elements.tags.tagInput?.commit();
    const body = new FormData(createForm);
    const aliases = createForm.elements.search_aliases.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    body.set("search_aliases", JSON.stringify(aliases));
    const response = await fetch("/api/entries/manual", {
      method: "POST",
      body,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      createError.textContent = errorMessage(data, T.createFailed);
      return;
    }
    createDialog.close();
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
    await loadMeta();
    await loadEntries(true);
    await openEntry(data.key);
    showToast(T.created);
  } catch (_error) {
    createError.textContent = T.createFailed;
  } finally {
    saveCreate.disabled = false;
  }
});

function resetCreateForm() {
  createForm.reset();
  createForm.elements?.tags?.tagInput?.setValue("");
  createError.textContent = "";
  if (createPreviewUrl) URL.revokeObjectURL(createPreviewUrl);
  createPreviewUrl = "";
  createPreview.removeAttribute("src");
  createPreview.closest(".upload-field").classList.remove("has-preview");
}
