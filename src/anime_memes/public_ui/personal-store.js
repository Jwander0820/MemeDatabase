/* 個人梗圖只寫入 IndexedDB；備份為帶版本的 JSON，不使用網路 API。 */
(() => {
  'use strict';
  const DB_NAME = 'memedatabase-personal';
  const VERSION = 1, MAX_IMAGE = 10 * 1024 ** 2, MAX_BACKUP = 100 * 1024 ** 2;
  const MAX_TOTAL = 60 * 1024 ** 2, MAX_ITEMS = 500;
  const LIMITS = {anime_title: 240, speaker: 240, quote_text: 4000, display_title: 240,
    search_aliases: 2000, tags: 1200, source_page: 2000, notes: 4000};
  const TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'};

  function fields(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('梗圖欄位格式不符');
    const result = {};
    for (const [key, max] of Object.entries(LIMITS)) {
      const text = value[key] ?? '';
      if (typeof text !== 'string' || text.length > max) throw new Error(`${key} 欄位過長或格式不符`);
      // JSON 備份可能帶有孤立 UTF-16 surrogate，修正後才能安全編碼 mailto。
      result[key] = text.trim().replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]|[\uD800-\uDFFF]/g, part => part.length === 2 ? part : '\uFFFD');
    }
    if (result.source_page && !['http:', 'https:'].includes(new URL(result.source_page).protocol)) throw new Error('圖片出處只接受 HTTP／HTTPS 網址');
    return result;
  }

  async function image(blob) {
    if (!(blob instanceof Blob) || !Object.hasOwn(TYPES, blob.type) || !blob.size || blob.size > MAX_IMAGE) throw new Error('請選擇 10 MB 以內的 JPG、PNG、WebP 或 GIF 圖片');
    const bytes = new Uint8Array(await blob.slice(0, 16).arrayBuffer());
    const text = String.fromCharCode(...bytes);
    const valid = blob.type === 'image/jpeg' ? bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 :
      blob.type === 'image/png' ? [137,80,78,71,13,10,26,10].every((n, i) => bytes[i] === n) :
      blob.type === 'image/gif' ? /^GIF8[79]a/.test(text) : text.startsWith('RIFF') && text.slice(8, 12) === 'WEBP';
    if (!valid) throw new Error('圖片內容與檔案格式不符');
    if (globalThis.createImageBitmap) {
      let bitmap;
      try {
        bitmap = await createImageBitmap(blob);
        if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 40000000) throw new Error('圖片尺寸過大');
      } catch { throw new Error('圖片無法解碼，或尺寸超過 4,000 萬像素'); }
      finally { bitmap?.close(); }
    }
    return blob;
  }

  function fileName(name, type) {
    const base = String(name || 'meme').replace(/[\\/\x00-\x1f<>:"|?*]/g, '_').slice(0, 140).replace(/\.[^.]*$/, '');
    return (base || 'meme') + TYPES[type];
  }

  function open() {
    return new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) { reject(new Error('此瀏覽器不支援本地梗圖儲存')); return; }
      const request = indexedDB.open(DB_NAME, VERSION);
      let abandoned = false;
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('memes')) request.result.createObjectStore('memes', {keyPath: 'id'});
      };
      request.onblocked = () => { abandoned = true; reject(new Error('請關閉其他開啟本站的分頁後重試，原資料不會被刪除')); };
      request.onerror = () => reject(new Error(request.error?.name === 'VersionError'
        ? '本地資料來自較新版網站，請更新網站版本；資料尚未變更'
        : '無法開啟本地儲存，請檢查瀏覽器隱私或儲存設定'));
      request.onsuccess = () => {
        const db = request.result;
        if (abandoned) { db.close(); return; }
        db.onversionchange = () => db.close(); resolve(db);
      };
    });
  }

  async function transaction(mode, work) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('memes', mode); let value;
      tx.oncomplete = () => { db.close(); resolve(value); };
      tx.onabort = tx.onerror = () => {
        db.close(); reject(new Error(tx.error?.name === 'QuotaExceededError'
          ? '瀏覽器儲存空間不足，操作未完成；請先匯出備份再整理圖片'
          : '本地儲存未完成，原有資料仍保留；請檢查空間或關閉其他分頁後重試'));
      };
      try { work(tx.objectStore('memes'), result => { value = result; }); }
      catch (error) { tx.abort(); reject(error); }
    });
  }

  const all = () => transaction('readonly', (store, done) => { const r = store.getAll(); r.onsuccess = () => done(r.result); });
  async function save(value, blob, previous = null) {
    const record = {id: previous?.id || crypto.randomUUID(), fields: fields(value), image: await image(blob),
      name: fileName(blob.name || previous?.name, blob.type), createdAt: previous?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString()};
    return transaction('readwrite', (store, done) => {
      const r = store.getAll();
      r.onsuccess = () => {
        const others = r.result.filter(item => item.id !== record.id);
        if (others.length >= MAX_ITEMS || others.reduce((n, item) => n + item.image.size, blob.size) > MAX_TOTAL) {
          store.transaction.abort(); return;
        }
        store.put(record); done(record);
      };
    });
  }
  const remove = id => transaction('readwrite', (store, done) => { store.delete(id); done(true); });

  async function base64(blob) {
    const bytes = new Uint8Array(await blob.arrayBuffer()); const chunks = [];
    for (let i = 0; i < bytes.length; i += 16384) chunks.push(String.fromCharCode(...bytes.subarray(i, i + 16384)));
    return btoa(chunks.join(''));
  }
  async function encode(records) {
    const items = [];
    for (const item of records) items.push({...item, image: {type: item.image.type, base64: await base64(item.image)}});
    const blob = new Blob([JSON.stringify({format: DB_NAME, version: VERSION, exportedAt: new Date().toISOString(), items})], {type: 'application/json'});
    if (blob.size > MAX_BACKUP) throw new Error('備份超過 100 MB 上限，請先分批下載圖片');
    return blob;
  }

  async function decode(text) {
    if (typeof text !== 'string' || text.length > MAX_BACKUP) throw new Error('備份超過 100 MB 上限');
    let data; try { data = JSON.parse(text); } catch { throw new Error('備份不是有效的 JSON 檔案'); }
    if (data?.format !== DB_NAME || data.version !== VERSION) throw new Error('不支援此備份格式版本；請保留原檔並使用相容網站版本，現有資料未變更');
    if (!Array.isArray(data.items) || data.items.length > MAX_ITEMS) throw new Error('備份筆數超過 500 筆或格式不符');
    const ids = new Set(), records = []; let total = 0;
    for (const item of data.items) {
      if (!item || typeof item.id !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(item.id) || ids.has(item.id)) throw new Error('備份含有重複或不合法的圖片編號');
      ids.add(item.id);
      const value = item.image;
      if (!value || !Object.hasOwn(TYPES, value.type) || typeof value.base64 !== 'string' || value.base64.length > MAX_IMAGE * 4 / 3 + 4 ||
          value.base64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value.base64)) throw new Error('備份圖片格式不符或檔案過大');
      const binary = atob(value.base64);
      total += binary.length;
      if (total > MAX_TOTAL) throw new Error('圖片總量超過 60 MB 上限');
      const blob = await image(new Blob([Uint8Array.from(binary, char => char.charCodeAt(0))], {type: value.type}));
      if (typeof item.createdAt !== 'string' || !Number.isFinite(Date.parse(item.createdAt)) ||
          typeof item.updatedAt !== 'string' || !Number.isFinite(Date.parse(item.updatedAt))) throw new Error('備份日期格式不符');
      records.push({id: item.id, fields: fields(item.fields), image: blob, name: fileName(item.name, blob.type),
        createdAt: new Date(item.createdAt).toISOString(), updatedAt: new Date(item.updatedAt).toISOString()});
    }
    return records;
  }

  async function importBackup(file) {
    if (!file || file.size > MAX_BACKUP) throw new Error('請選擇 100 MB 以內的 JSON 備份');
    const records = await decode(await file.text()); // 全部驗證後才開始寫入；任何失敗都不做半套匯入。
    return transaction('readwrite', (store, done) => {
      const r = store.getAll();
      r.onsuccess = () => {
        const existing = new Set(r.result.map(item => item.id));
        const added = records.filter(item => !existing.has(item.id));
        const total = [...r.result, ...added];
        if (total.length > MAX_ITEMS || total.reduce((n, item) => n + item.image.size, 0) > MAX_TOTAL) { store.transaction.abort(); return; }
        added.forEach(item => store.add(item)); done({added: added.length, skipped: records.length - added.length});
      };
    });
  }

  function submission(record) {
    const f = fields(record.fields);
    const body = ['梗圖投稿（請手動附上圖片）', '圖片檔名：' + record.name,
      ...Object.keys(LIMITS).map(key => ({anime_title:'作品',speaker:'角色',quote_text:'台詞',display_title:'梗圖名稱',
        search_aliases:'替代搜尋詞',tags:'建議標籤',source_page:'圖片來源',notes:'備註'}[key]) + '：' + f[key]),
      '', '我願意提供此圖供管理者審核是否公開收錄。'].join('\n');
    const subject = '梗圖投稿：' + (f.display_title || f.anime_title || '新增梗圖').replace(/[\r\n]/g, ' ').slice(0, 80);
    const prefix = 'mailto:meme@jwander.net?subject=' + encodeURIComponent(subject);
    const full = prefix + '&body=' + encodeURIComponent(body);
    return {body, href: full.length <= 6000 ? full : prefix + '&body=' + encodeURIComponent('請貼上已複製的投稿內容，並手動附上圖片。'), truncated: full.length > 6000};
  }
  globalThis.MemePersonalData = {all, save, remove, encode, decode, importBackup, fields, image, submission, MAX_BACKUP};
  if (typeof module !== 'undefined') module.exports = globalThis.MemePersonalData;
})();
