const publicationDialog = document.querySelector("#publicationDialog");
const publicationStatus = document.querySelector("#publicationStatus");
const publicationReport = document.querySelector("#publicationReport");
const checkPublication = document.querySelector("#checkPublication");
const exportPublication = document.querySelector("#exportPublication");
const activatePublication = document.querySelector("#activatePublication");
let exportedVersion = "";

// 公開選取有變動時，先前的檢查／匯出結果都失效，必須重新檢查。
function invalidatePublication() {
  exportPublication.disabled = activatePublication.disabled = true;
  exportedVersion = "";
  publicationReport.replaceChildren();
  publicationStatus.textContent = "公開設定已更新，請重新檢查。";
}

async function publicationRequest(path, method, data) {
  const options = {method};
  if (data !== undefined) {
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.error || `請求失敗（HTTP ${response.status}）`);
    error.report = body.report;
    throw error;
  }
  return body;
}

async function bindPublication(form, key) {
  const fields = form.querySelector("fieldset");
  const output = form.querySelector("output");
  const path = "/api/entries/" + encodeURIComponent(key) + "/publication";
  try {
    const settings = await publicationRequest(path, "GET");
    form.elements.selected.checked = settings.publish_selected;
    form.elements.original.value = settings.public_original_url || "";
    output.textContent = settings.r2_url ? "R2 大圖：" + settings.r2_url : "";
    fields.disabled = false;
  } catch (error) {
    output.textContent = "讀取失敗，請重新開啟此圖。" + error.message;
    return;
  }
  form.onsubmit = async event => {
    event.preventDefault();
    fields.disabled = true;
    try {
      await publicationRequest(path, "PATCH", {
        publish_selected: form.elements.selected.checked,
        public_original_url: form.elements.original.value.trim() || null
      });
      output.textContent = "已儲存，下次匯出並啟用後生效。";
      // 同步更新卡片上的「公開」標記與篩選結果（保留抽屜內容，不重開詳情）。
      if (typeof loadEntries === "function") loadEntries(true);
      invalidatePublication();
    } catch (error) {
      output.textContent = error.message;
    } finally {
      fields.disabled = false;
    }
  };
}

function renderPublicationReport(report) {
  publicationReport.replaceChildren();
  const summary = document.createElement("strong");
  summary.textContent = `已選取 ${report.count} 筆，${report.valid ? "檢查通過" : "請修正以下錯誤"}`;
  publicationReport.append(summary);
  for (const message of report.errors || []) {
    const line = document.createElement("p");
    line.textContent = "錯誤：" + message;
    publicationReport.append(line);
  }
  for (const warning of report.warnings) {
    const line = document.createElement("p");
    line.textContent = warning;
    publicationReport.append(line);
  }
  for (const item of report.items) {
    if (!item.errors.length && !item.warnings.length) continue;
    const line = document.createElement("p");
    const link = document.createElement("button");
    link.textContent = item.key;
    link.onclick = () => {
      publicationDialog.close();
      openEntry(item.key);
    };
    line.append(link, " ", ...item.errors.map(s => "錯誤：" + s + " "), ...item.warnings.map(s => "提醒：" + s + " "));
    publicationReport.append(line);
  }
}

document.querySelector("#openPublication").onclick = () => publicationDialog.showModal();
async function runPublication(action) {
  checkPublication.disabled = exportPublication.disabled = activatePublication.disabled = true;
  if (action !== "activate") {
    exportedVersion = "";
    publicationReport.replaceChildren();
  }
  publicationStatus.textContent = "處理中…";
  try {
    const data = await publicationRequest("/api/publication/" + action, "POST",
      action === "activate" ? {version: exportedVersion} : {});
    if (action === "activate") {
      publicationStatus.textContent = `已啟用 ${data.version}，公開館藏共 ${data.count} 筆。`;
      exportedVersion = "";
    } else {
      const report = data.report || data;
      renderPublicationReport(report);
      exportedVersion = data.version || "";
      exportPublication.disabled = !report.valid || !!exportedVersion;
      const media = data.media;
      const mediaText = media ? `（已上傳 ${media.uploaded} 張、沿用 ${media.reused} 張至 R2）` : "";
      publicationStatus.textContent = exportedVersion ? `已匯出 ${exportedVersion}${mediaText}，等待啟用。` : "檢查完成";
    }
  } catch (error) {
    if (error.report) renderPublicationReport(error.report);
    publicationStatus.textContent = "操作失敗：" + error.message;
  } finally {
    checkPublication.disabled = false;
    activatePublication.disabled = !exportedVersion;
  }
}
checkPublication.onclick = () => runPublication("check");
exportPublication.onclick = () => runPublication("export");
activatePublication.onclick = () => runPublication("activate");

// ---------- 徹底清除 ----------
const previewPurge = document.querySelector("#previewPurge");
const runPurge = document.querySelector("#runPurge");
const purgeReport = document.querySelector("#purgeReport");

function renderPurge(result) {
  const lines = [
    `保留版本：${result.kept_versions.join("、")}（目前啟用 ${result.active}）`,
    `${result.dry_run ? "將刪除" : "已刪除"}舊版本 ${result.removed_versions.length} 個、縮圖 ${result.removed_media} 張、R2 大圖 ${result.r2_deleted.length} 張`,
  ];
  if (result.r2_skipped.length) lines.push(`未處理的 R2 大圖 ${result.r2_skipped.length} 張（R2 未設定或不在本專案前綴底下）`);
  result.errors.forEach((error) => lines.push("錯誤：" + error));
  purgeReport.replaceChildren(...lines.map((text) => {
    const line = document.createElement("p");
    line.textContent = text;
    return line;
  }));
  if (!result.dry_run && result.r2_deleted.length) {
    // 刪除後 Cloudflare 邊緣快取可能仍保留副本，列出網址方便到後台依網址清除快取。
    const note = document.createElement("p");
    note.textContent = "請到 Cloudflare 後台「快取 → 清除快取 → 自訂清除（依網址）」貼上以下網址：";
    const urls = document.createElement("textarea");
    urls.readOnly = true;
    urls.rows = Math.min(6, result.r2_deleted.length);
    urls.value = result.r2_deleted.join("\n");
    purgeReport.append(note, urls);
  }
}

async function purge(dryRun) {
  previewPurge.disabled = runPurge.disabled = true;
  try {
    const result = await publicationRequest("/api/publication/purge", "POST", {dry_run: dryRun});
    renderPurge(result);
    const nothing = !result.removed_versions.length && !result.removed_media && !result.r2_deleted.length;
    runPurge.disabled = !dryRun || nothing;
    if (dryRun && nothing) purgeReport.append(Object.assign(document.createElement("p"), {textContent: "沒有需要清除的內容。"}));
  } catch (error) {
    purgeReport.textContent = "操作失敗：" + error.message;
  } finally {
    previewPurge.disabled = false;
  }
}

previewPurge.onclick = () => purge(true);
runPurge.onclick = () => {
  if (confirm("確定刪除？舊版本刪除後無法退回，R2 大圖也會一併刪除。")) purge(false);
};
