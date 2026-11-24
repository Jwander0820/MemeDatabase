const publicationDialog = document.querySelector("#publicationDialog");
const publicationStatus = document.querySelector("#publicationStatus");
const publicationReport = document.querySelector("#publicationReport");
const checkPublication = document.querySelector("#checkPublication");
const exportPublication = document.querySelector("#exportPublication");
const activatePublication = document.querySelector("#activatePublication");
let exportedVersion = "";

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
      exportPublication.disabled = activatePublication.disabled = true;
      exportedVersion = "";
      publicationReport.replaceChildren();
      publicationStatus.textContent = "公開設定已更新，請重新檢查。";
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
