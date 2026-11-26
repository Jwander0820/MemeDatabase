const test = require('node:test');
const assert = require('node:assert/strict');
const store = require('../src/anime_memes/public_ui/personal-store.js');
const png = () => new Blob([Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aA1kAAAAASUVORK5CYII=', 'base64')], {type: 'image/png'});
function record() {
  return {id: 'local-test-1', fields: store.fields({anime_title: '測試動畫', quote_text: '本地台詞', tags: '開心, 回應'}),
    image: png(), name: 'test.png', createdAt: '2026-11-26T00:00:00Z', updatedAt: '2026-11-26T01:00:00Z'};
}

test('含格式版本的備份可還原圖片與欄位，不呼叫網路', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('local operations must never upload'); };
  try {
    const backup = await store.encode([record()]);
    const json = JSON.parse(await backup.text());
    assert.equal(json.version, 1);
    const [restored] = await store.decode(await backup.text());
    assert.equal(restored.fields.quote_text, '本地台詞');
    assert.equal(restored.image.type, 'image/png');
    assert.deepEqual(Buffer.from(await restored.image.arrayBuffer()), Buffer.from(await png().arrayBuffer()));
  } finally { globalThis.fetch = originalFetch; }
});

test('未知格式版本拒絕匯入，不開啟 IndexedDB 或覆寫資料', async () => {
  const original = globalThis.indexedDB;
  globalThis.indexedDB = {open() { throw new Error('must not write'); }};
  try {
    const file = new Blob([JSON.stringify({format:'memedatabase-personal',version:99,items:[]})]);
    await assert.rejects(store.importBackup(file), /不支援此備份格式版本/);
  } finally { globalThis.indexedDB = original; }
});

test('壞圖片、SVG、假 MIME、重複 ID 與不安全來源會被拒絕', async () => {
  await assert.rejects(store.image(new Blob(['<svg/>'], {type:'image/svg+xml'})), /請選擇/);
  await assert.rejects(store.image(new Blob(['<script/>'], {type:'image/png'})), /內容與檔案格式不符/);
  assert.throws(() => store.fields({source_page:'javascript:alert(1)'}), /HTTP/);
  const backup = JSON.parse(await (await store.encode([record()])).text());
  backup.items.push(backup.items[0]);
  await assert.rejects(store.decode(JSON.stringify(backup)), /重複/);
});

test('所有備份資料驗證完成前不開始匯入', async () => {
  const backup = JSON.parse(await (await store.encode([record()])).text());
  backup.items.push({...backup.items[0], id:'bad-second',image:{type:'image/png',base64:'AAAA'}});
  const original = globalThis.indexedDB;
  let calls = 0; globalThis.indexedDB = {open() { calls++; throw new Error('must not write'); }};
  try {
    await assert.rejects(store.importBackup(new Blob([JSON.stringify(backup)])), /圖片內容/);
    assert.equal(calls, 0);
  } finally { globalThis.indexedDB = original; }
});

test('投稿固定收件人，欄位內容不能插入 mailto 郵件標頭', () => {
  const value = record(); value.fields.display_title = '測試\nBcc:someone@example.test';
  const draft = store.submission(value);
  const url = new URL(draft.href);
  assert.equal(url.pathname, 'meme@jwander.net');
  assert.equal(url.searchParams.has('bcc'), false);
  assert.match(draft.body, /本地台詞/);
  assert.match(draft.body, /請手動附上圖片/);
});

test('長投稿保留完整可複製文字，縮短 mailto 以避免截斷', () => {
  const value = record(); value.fields.quote_text = '梗'.repeat(3000);
  const draft = store.submission(value);
  assert.equal(draft.truncated, true);
  assert.match(draft.body, /梗{3000}/);
  assert.ok(draft.href.length < 6000);
});

test('備份中的無效 Unicode 不會讓郵件草稿建立失敗', () => {
  const value = record(); value.fields.display_title = '測試\ud800';
  assert.doesNotThrow(() => store.submission(value));
});
