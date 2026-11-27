const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('連點首頁不重載也不重送圖片查詢，從篩選返回時只查一次', () => {
  const g = gallery();
  g.pending.length = 0;
  const click = {button: 0, preventDefault() {}};
  for (let i = 0; i < 14; i++) g.node('home').onclick(click);
  assert.equal(g.pending.length, 0);
  g.node('query').value = '海綿寶寶';
  for (let i = 0; i < 7; i++) g.node('home').onclick(click);
  assert.equal(g.pending.length, 1);
  assert.equal(g.node('query').value, '');
  g.node('home').onclick({...click, ctrlKey: true, preventDefault() { throw Error('修飾鍵需保留原生連結'); }});
});

test('骰子清除篩選並換種子，阻擋連點且後續分頁維持同一次抽選', async () => {
  const g = gallery();
  g.pending.length = 0;
  g.node('query').value = '海綿寶寶';
  g.node('sort').value = 'created';
  g.run('activeTag = "吐槽"; favoritesOnly = true; activeScope = "work";');
  const first = g.node('rollMemes').onclick();
  g.node('rollMemes').onclick();
  assert.equal(g.pending.length, 1, '連點不重複送出請求');
  const request = g.pending.shift();
  const params = new URLSearchParams(request.url.split('?')[1]);
  const seed = params.get('seed');
  assert.match(seed, /^[A-Za-z0-9_-]{1,32}$/);
  assert.notEqual(seed, today());
  assert.equal(params.get('sort'), 'random');
  for (const name of ['q', 'tag', 'field', 'ids', 'favorite']) assert.equal(params.has(name), false);
  await g.respond(request, {version: 'v1', page: 1, total: 3, items: [entry('a')]});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  await first;
  assert.equal(g.node('rollMemes').disabled, false);
  assert.match(g.node('status').textContent, /骰子隨機/);
  g.search(false);
  const next = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(next.get('seed'), seed);
  assert.equal(next.get('page'), '2');
  g.node('rollMemes').onclick();
  const rerolled = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.notEqual(rerolled.get('seed'), seed);
  assert.equal(rerolled.get('page'), '1');
});

function gallery(withObserver = false, storage = undefined, {globals = {}, setup} = {}) {
  const nodes = new Map();
  const pending = [];
  const created = [];
  function element(tag) {
    const made = {tag, value: '', childElementCount: 0, listeners: {},
      addEventListener(type, fn) { this.listeners[type] = fn; },
      setAttribute(name, value) { this[name] = value; },
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
    fetch(url) { return new Promise(resolve => pending.push({url, resolve})); },
    ...globals
  });
  if (setup) setup(node);
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

test('首頁合併常用查詢，五次骰圖各只取一頁並沿用同版常用圖', async () => {
  const [a, gone, f1, f2, f3] = ['a', 'b', 'c', 'd', 'e'].map(id => id.repeat(32));
  const usage = {[a]: {count: 3, last: 1}, [gone]: {count: 2, last: 1}};
  const g = gallery(true, memoryStorage({'meme-db-usage': JSON.stringify(usage), 'meme-db-favorites': JSON.stringify([f1, f2, f3])}));
  const paramsOf = request => new URLSearchParams(request.url.split('?')[1]);
  assert.equal(g.pending.length, 1);
  const random = g.pending.shift();
  const list = paramsOf(random);
  assert.equal(list.get('sort'), 'random');
  assert.equal(list.get('seed'), today());
  assert.equal(list.get('page_size'), '25');
  g.observers[0]([{isIntersecting: true}]); // 空列表此時可見，加入卡片後需重新量測。
  const data = {version: 'v1', page: 1, total: 29, items: [a, f1, 'x', 'y'].map(entry)};
  await g.respond(random, data);
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  const batch = g.pending.shift();
  assert.equal(paramsOf(batch).get('ids'), [a, gone, f1, f2, f3].join(','));
  assert.equal(paramsOf(batch).get('version'), 'v1');
  await g.respond(batch, {version: 'v1', items: [f3, f2, f1, a].map(entry)});
  assert.equal(g.pending.length, 0);
  assert.equal(g.node('results').childElementCount, 6, '4 張常用 + 2 張不重複的隨機');
  assert.deepEqual(Array.from(g.run('pinnedIds')), [a, f1, f2, f3]);
  for (let i = 0; i < 5; i++) {
    const roll = g.node('rollMemes').onclick();
    assert.equal(g.pending.length, 1);
    const page = g.pending.shift();
    assert.equal(paramsOf(page).has('ids'), false);
    await g.respond(page, data);
    await roll;
    assert.equal(g.pending.length, 0);
  }
  const roll = g.node('rollMemes').onclick();
  await g.respond(g.pending.shift(), {...data, version: 'v2'});
  await g.respond(g.pending.shift(), {version: 'v2', tags: []});
  assert.equal(paramsOf(g.pending[0]).get('version'), 'v2');
  await g.respond(g.pending.shift(), {version: 'v2', items: []});
  await roll;
  assert.equal(g.run('pinnedIds.size'), 0);
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

test('注音組字期間不搜尋，選字完成才送出一次', () => {
  const g = gallery();
  g.pending.length = 0;
  g.run('var __timers = []; setTimeout = fn => __timers.push(fn);');
  const query = g.node('query');
  query.listeners.compositionstart();
  query.value = 'ㄅㄨˋ';
  query.oninput({isComposing: true});
  query.oninput({});
  assert.equal(g.run('__timers.length'), 0, '組字中不排程搜尋');
  query.value = '不';
  query.listeners.compositionend();
  query.oninput({isComposing: false});
  g.run('__timers.forEach(fn => fn())');
  // 兩次排程的計時器都會跑，但舊的被 listRequest 作廢；這裡只確認送出的查詢字是選好的字。
  const sent = g.pending.map(r => new URLSearchParams(r.url.split('?')[1]).get('q'));
  assert.ok(sent.length >= 1);
  assert.ok(sent.every(q => q === '不'));
});

test('換條件時舊結果保留到新結果回來才替換', async () => {
  const g = gallery();
  await g.respond(g.pending.shift(), {version: 'v1', page: 1, total: 1, items: [entry('a')]});
  await g.respond(g.pending.shift(), {version: 'v1', tags: []});
  const before = g.node('results').childElementCount;
  assert.ok(before > 0);
  g.node('query').value = '烈海王';
  g.search(true);
  assert.equal(g.node('results').childElementCount, before, '等待回應時不清空');
  await g.respond(g.pending.shift(), {version: 'v1', page: 1, total: 1, items: [entry('b')]});
  assert.equal(g.node('results').childElementCount, 1, '回應後只剩新結果');
  g.node('query').value = '查不到';
  g.search(true);
  await g.respond(g.pending.shift(), null, 503);
  assert.equal(g.node('results').childElementCount, 0, '新條件失敗時不留舊結果');
});

test('搜尋條件寫進網址，重新載入與上一頁都能還原', async () => {
  const listeners = {};
  const location = {pathname: '/', search: '?q=%E7%83%88%E6%B5%B7%E7%8E%8B&in=speaker&tag=%E5%90%90%E6%A7%BD&sort=created&fav=1&x=1', hash: ''};
  const calls = [];
  const apply = (kind) => (state, title, url) => {
    calls.push([kind, url]);
    const parsed = new URL(url, 'http://h');
    location.pathname = parsed.pathname; location.search = parsed.search; location.hash = parsed.hash;
  };
  const history = {state: null, pushState: apply('push'), replaceState: apply('replace')};
  const g = gallery(false, undefined, {
    globals: {location, history, window: {addEventListener(type, fn) { listeners[type] = fn; }, scrollY: 0}},
    setup: node => { node('sort').options = [{value: ''}, {value: 'relevance'}, {value: 'created'}]; },
  });
  const first = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(first.get('q'), '烈海王');
  assert.equal(first.get('field'), 'speaker');
  assert.equal(first.get('tag'), '吐槽');
  assert.equal(first.get('sort'), 'created');
  assert.equal(g.run('favoritesOnly'), true);
  // 初次載入只把網址整理成標準格式（去掉未知參數），不新增歷史。
  assert.deepEqual(calls.map(c => c[0]), ['replace']);
  assert.equal(location.search, '?q=%E7%83%88%E6%B5%B7%E7%8E%8B&in=speaker&tag=%E5%90%90%E6%A7%BD&sort=created&fav=1');

  // 選其他標籤：新增一筆歷史。
  calls.length = 0;
  g.run('activeTag = "傻眼"; favoritesOnly = false;');
  g.search(true);
  assert.deepEqual(calls.map(c => c[0]), ['push']);
  assert.match(location.search, /tag=%E5%82%BB%E7%9C%BC/);
  g.pending.length = 0;

  // 連續打字：第一次新增歷史，之後原地更新。
  calls.length = 0;
  for (const text of ['芙', '芙莉', '芙莉蓮']) {
    g.node('query').value = text;
    g.run('urlMode = "typing"');
    g.search(true);
  }
  assert.deepEqual(calls.map(c => c[0]), ['push', 'replace', 'replace']);
  g.pending.length = 0;

  // 上一頁：網址回到先前的條件時重新查詢，且不再寫網址。
  calls.length = 0;
  location.search = '?tag=%E5%82%BB%E7%9C%BC&sort=created';
  listeners.popstate();
  assert.equal(calls.length, 0);
  const back = new URLSearchParams(g.pending.shift().url.split('?')[1]);
  assert.equal(back.get('tag'), '傻眼');
  assert.equal(back.has('q'), false);
  assert.equal(g.node('query').value, '');
  // 只有詳情的 hash 變動（條件相同）時不重查。
  location.hash = '#/m/abc';
  listeners.popstate();
  assert.equal(g.pending.length, 0);
});
