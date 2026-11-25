"use strict";
// ---------- 使用紀錄 ----------
// 只存在訪客自己瀏覽器的 localStorage，不上傳任何資料。
// 內容為 {id: {count, last}}；本機簡潔瀏覽的 id 是管理 key，與公開 ID 不同，所以分開存放。
const USAGE_KEY = "meme-db-usage";
const USAGE_LOCAL_KEY = "meme-db-usage-local";
const MAX_USAGE = 100;

function readUsage() {
  const usage = {};
  try {
    const saved = JSON.parse(globalThis.localStorage?.getItem(localMode ? USAGE_LOCAL_KEY : USAGE_KEY) || "{}");
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return usage;
    for (const [id, entry] of Object.entries(saved)) {
      if (entry && Number.isFinite(entry.count) && entry.count > 0) {
        usage[id] = {count: entry.count, last: Number.isFinite(entry.last) ? entry.last : 0};
      }
    }
  } catch {}
  return usage;
}

// 排序：次數多者優先，次數相同時最近使用者優先。
const usageOrder = usage => (a, b) => usage[b].count - usage[a].count || usage[b].last - usage[a].last;

function recordUsage(id) {
  if (typeof id !== "string" || !id) return;
  const usage = readUsage();
  usage[id] = {count: (usage[id]?.count || 0) + 1, last: Date.now()};
  // 超過上限時淘汰次數最少、最久沒用的；剛記錄的這一筆不淘汰。
  const others = Object.keys(usage).filter(key => key !== id).sort(usageOrder(usage));
  while (others.length + 1 > MAX_USAGE) delete usage[others.pop()];
  try { globalThis.localStorage?.setItem(localMode ? USAGE_LOCAL_KEY : USAGE_KEY, JSON.stringify(usage)); } catch {}
}

function topUsed(n) {
  const usage = readUsage();
  return Object.keys(usage).sort(usageOrder(usage)).slice(0, n);
}
