const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function gallery(withObserver = false, storage = undefined) {
  const nodes = new Map();
  const pending = [];
  const created = [];
  function element(tag) {
    const made = {tag, value: '', childElementCount: 0, addEventListener() {},
      replaceChildren() { this.childElementCount = 0; },
      append(...items) { this.childElementCount += items.length; },
      add() {}};
    created.push(made);
    return made;
  }
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  }
  const observers = [];
  const extra = withObserver ? {
    IntersectionObserver: class { constructor(callback) { observers.push(callback); } observe() {} disconnect() {} },
    requestAnimationFrame() {}
  } : {};
  const context = vm.createContext({
    ...extra,
    ...(storage ? {localStorage: storage} : {}),
    document: {getElementById: node, createElement: element},
    window: {addEventListener() {}}, location: {hash: ''},
    clearTimeout() {}, URLSearchParams, Option: class {},
    fetch(url) { return new Promise(resolve => pending.push({url, resolve})); }
  });
  // 與 HTML 相同的載入順序；各檔共用同一個全域作用域。
  for (const name of ['infinite-scroll.js', 'gallery.js', 'gallery-favorites.js', 'gallery-usage.js', 'gallery-detail.js', 'gallery-init.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui', name), 'utf8'), context, {filename: name});
  }
  function respond(request, data, status = 200) {
    request.resolve({ok: status === 200, status, json: async () => data});
    return new Promise(resolve => setImmediate(resolve));
  }
  return {node, pending, respond, observers, created,
    run: code => vm.runInContext(code, context),
    search: reset => vm.runInContext('search(' + reset + ')', context),
    setScope: value => vm.runInContext('setScope(' + JSON.stringify(value) + ')', context)};
}

test('公開搜尋忽略舊回應，失效版本保留條件並從第一頁重查', async () => {
  const g = gallery();
  const old = g.pending.shift();
  g.node('query').value = '烈海王';
  g.search(true);
  const item = {id: 'a', resolved_title: '復活', tags: []};
  await g.respond(g.pending.shift(), {version: 'v2', page: 1, total: 2, items: [item]});
  await g.respond(g.pending.shift(), {version: 'v2', tags: []});
  await g.respond(old, {version: 'v1', page: 1, total: 0, items: []});
  assert.equal(g.node('status').textContent, '已顯示 1／2 張');
  g.node('more').onclick();
  const next = g.pending.shift();
  const params = new URLSearchParams(next.url.split('?')[1]);
  assert.equal(params.get('version'), 'v2');
  assert.equal(params.get('page'), '2');
  await g.respond(next, null, 410);
  assert.equal(g.node('more').textContent, '從最新館藏重新搜尋');
  g.node('more').onclick();
  const retry = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(retry.get('page'), '1');
  assert.equal(retry.get('q'), '烈海王');
  assert.equal(retry.has('version'), false);
});

test('搜尋範圍只在有查詢字時送出 field，並更新 placeholder', () => {
  const g = gallery();
  const paramsOf = request => new URLSearchParams(request.url.split('?')[1]);
  g.pending.length = 0;
  g.setScope('work');
  assert.equal(g.pending.length, 0, '沒有查詢字時切換範圍不重查');
  assert.equal(g.node('query').placeholder, '搜尋作品名稱');
  g.search(true);
  assert.equal(paramsOf(g.pending.shift()).has('field'), false);
  g.node('query').value = '烈海王';
  g.search(true);
  assert.equal(paramsOf(g.pending.shift()).get('field'), 'work');
  g.setScope('speaker');
  assert.equal(paramsOf(g.pending.shift()).get('field'), 'speaker');
  g.setScope('');
  assert.equal(paramsOf(g.pending.shift()).has('field'), false);
  assert.equal(g.node('query').placeholder, '搜尋台詞、作品、角色或標籤');
});

test('公開頁辨識 Nginx 的非 JSON 限流錯誤並提供重試', async () => {
  const g = gallery();
  await g.respond(g.pending.shift(), null, 429);
  assert.match(g.node('status').textContent, /過於頻繁/);
  assert.equal(g.node('more').hidden, false);
  assert.equal(g.node('more').textContent, '重試');
});

test('捲到底自動載入下一頁，失敗後不自動重試並顯示重試按鈕', async () => {
  const g = gallery(true);
  const item = id => ({id, resolved_title: id, tags: []});
  await g.respond(g.pending.shift(), {version: 'v1', page: 1, total: 3, items: [item('a')]});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  assert.equal(g.node('more').hidden, true, '有自動載入時不顯示按鈕');
  assert.equal(g.pending.length, 0, '哨兵尚未進入視窗不載入');
  g.observers[0]([{isIntersecting: true}]);
  const next = g.pending.shift();
  assert.equal(new URLSearchParams(next.url.split('?')[1]).get('page'), '2');
  await g.respond(next, {version: 'v1', page: 2, total: 3, items: [item('b')]});
  assert.equal(g.node('status').textContent, '已顯示 2／3 張');
  g.observers[0]([{isIntersecting: true}]);
  await g.respond(g.pending.shift(), null, 503);
  assert.equal(g.node('more').hidden, false);
  assert.equal(g.node('more').textContent, '重試');
  g.observers[0]([{isIntersecting: true}]);
  assert.equal(g.pending.length, 0, '失敗後不自動重試');
  g.node('more').onclick();
  const retry = g.pending.shift();
  assert.equal(new URLSearchParams(retry.url.split('?')[1]).get('page'), '3');
  await g.respond(retry, {version: 'v1', page: 3, total: 3, items: [item('c')]});
  assert.equal(g.node('more').hidden, true);
  assert.equal(g.node('loadHint').textContent, '已經到底了');
});

function memoryStorage(initial = {}) {
  const data = {...initial};
  return {data, getItem: key => (key in data ? data[key] : null), setItem(key, value) { data[key] = String(value); }};
}

test('公開收藏顯示最近 200 張的限制與較早收藏數量，保留原有紀錄', async () => {
  const ids = Array.from({length: 201}, (_,index) => (index + 1).toString(16).padStart(32, '0'));
  const stored = JSON.stringify(ids);
  const storage = memoryStorage({'meme-db-favorites': stored});
  const g = gallery(false, storage);
  g.pending.length = 0;
  g.run('favoritesOnly = true');
  g.search(true);
  const request = g.pending.shift();
  const asked = new URLSearchParams(request.url.split('?')[1]).get('ids').split(',');
  assert.deepEqual(asked, ids.slice(0, 200));
  await g.respond(request, {version: 'v1', page: 1, total: 199, items: []});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  assert.match(g.node('status').textContent, /最多顯示最近 200 張/);
  assert.match(g.node('status').textContent, /另有 1 張較早的收藏仍保留/);
  assert.match(g.node('status').textContent, /另有 1 張收藏已下架/);
  assert.equal(storage.data['meme-db-favorites'], stored);
});

test('本機收藏沿用資料庫查詢，不套用公開收藏的顯示上限', async () => {
  const g = gallery();
  g.pending.length = 0;
  g.run('favoritesOnly = true; localMode = true');
  g.search(true);
  const request = g.pending.shift();
  const params = new URLSearchParams(request.url.split('?')[1]);
  assert.equal(params.get('favorite'), '1');
  assert.equal(params.has('ids'), false);
  await g.respond(request, {version: 'local', page: 1, total: 201, items: []});
  await g.respond(g.pending.shift(), {version: 'local', tags: [], mode: 'local'});
  assert.doesNotMatch(g.node('status').textContent, /200 張|此瀏覽器/);
});
const today = () => {
  const now = new Date();
  const pad = value => String(value).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};
const entry = id => ({id, resolved_title: id, tags: []});

test('recordUsage 與 topUsed 依次數再依最近排序，並限制最多 100 筆', () => {
  const storage = memoryStorage();
  const g = gallery(false, storage);
  g.run("var tick = 0; Date.now = () => ++tick");
  g.run("recordUsage('a'); recordUsage('b'); recordUsage('b'); recordUsage('c')");
  assert.deepEqual(Array.from(g.run('topUsed(5)')), ['b', 'c', 'a']);
  assert.deepEqual(Array.from(g.run('topUsed(1)')), ['b']);
  for (let i = 0; i < 120; i += 1) g.run(`recordUsage('x${i}')`);
  const saved = JSON.parse(storage.data['meme-db-usage']);
  assert.equal(Object.keys(saved).length, 100);
  assert.ok(saved.b, '次數高的保留');
  assert.ok(saved.x119, '剛記錄的保留');
  assert.equal(saved.x0, undefined, '次數最少且最舊的被淘汰');
});

test('首頁先請求常用單筆再請求每日隨機，並剔除重複的 id', async () => {
  const usage = {a: {count: 3, last: 1}, gone: {count: 2, last: 1}};
  const g = gallery(false, memoryStorage({'meme-db-usage': JSON.stringify(usage), 'meme-db-favorites': JSON.stringify(['f1'])}));
  const urls = g.pending.map(request => request.url);
  assert.equal(urls[0], '/api/v1/entries/a?');
  assert.equal(urls[1], '/api/v1/entries/gone?');
  assert.equal(urls[2], '/api/v1/entries/f1?');
  const list = new URLSearchParams(urls[3].split('?')[1]);
  assert.equal(list.get('sort'), 'random');
  assert.equal(list.get('seed'), today());
  const [a, gone, f1, random] = g.pending.splice(0);
  await g.respond(a, {item: entry('a')});
  await g.respond(gone, null, 404);
  await g.respond(f1, {item: entry('f1')});
  await g.respond(random, {version: 'v1', page: 1, total: 4, items: [entry('a'), entry('x'), entry('f1'), entry('y')]});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  assert.equal(g.node('results').childElementCount, 4, '2 張常用 + 2 張不重複的隨機');
  assert.match(g.node('status').textContent, /首頁推薦：常用 2 張＋每日隨機/);
  assert.match(g.node('status').textContent, /已顯示 4／4 張/);
  assert.ok(g.created.some(node => node.className === 'badge-pinned' && node.textContent === '常用'));
});

test('有搜尋字或選標籤時不帶 sort=random', () => {
  const g = gallery();
  g.pending.length = 0;
  g.node('query').value = '烈海王';
  g.search(true);
  const searched = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(searched.has('sort'), false);
  assert.equal(searched.has('seed'), false);
  g.node('query').value = '';
  g.run("activeTag = '貓'");
  g.search(true);
  const tagged = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(tagged.has('sort'), false);
  assert.equal(tagged.get('tag'), '貓');
});

test('最近新增 chip 送出 sort=created 並與排序選單同步', () => {
  const g = gallery();
  g.pending.length = 0;
  const chip = () => g.created.filter(node => node.tag === 'button' && node.textContent === '最近新增').pop();
  g.run('renderTags()');
  assert.equal(chip().ariaPressed, 'false');
  chip().onclick();
  assert.equal(g.node('sort').value, 'created');
  assert.equal(new URLSearchParams(g.pending.shift().url.split('?')[1]).get('sort'), 'created');
  assert.equal(chip().ariaPressed, 'true');
  g.node('sort').value = 'title';
  g.run('renderTags()');
  assert.equal(chip().ariaPressed, 'false', '選單改成其他值時 chip 取消');
  g.node('sort').value = 'created';
  g.node('sort').onchange();
  assert.equal(chip().ariaPressed, 'true', '選單選最近新增時 chip 亮起');
  g.pending.length = 0;
  chip().onclick();
  assert.equal(g.node('sort').value, '', '再點一次回到首頁推薦');
  assert.equal(new URLSearchParams(g.pending.pop().url.split('?')[1]).get('sort'), 'random');
});

test('localStorage 拋出例外時首頁與使用紀錄不會壞', async () => {
  const broken = {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }};
  const g = gallery(false, broken);
  g.run("recordUsage('a')");
  assert.deepEqual(Array.from(g.run('topUsed(5)')), []);
  await g.respond(g.pending.shift(), {version: 'v1', page: 1, total: 1, items: [entry('a')]});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  assert.equal(g.node('status').textContent, '已顯示 1／1 張');
});
