const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}

async function harness({observer = false} = {}) {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: '', checked: false, disabled: false, innerHTML: '', textContent: '',
      handlers: {}, classList: {add() {}, remove() {}},
      addEventListener(type, handler) { this.handlers[type] = handler; },
      setAttribute() {}, appendChild() {},
      querySelector(selector) { return node(`${selector}@child`); },
    });
    return nodes.get(selector);
  }
  let handler = async url => ({ok: true, json: async () => url === '/api/meta'
    ? {tags: []} : {items: [], page: 1, total: 0}});
  const context = vm.createContext({
    document: {querySelector: selector => (selector === 'dialog[open]' ? null : node(selector)), createElement: () => node(Symbol()), querySelectorAll: () => []},
    fetch: (...args) => handler(...args), URLSearchParams, setTimeout: () => 0, clearTimeout() {},
  });
  context.window = context;
  context.requestAnimationFrame = () => 0;
  const listeners = {};
  context.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
  context.bindPublication = () => {};
  context.confirm = () => true;
  let trigger = null;
  if (observer) {
    // 假的 IntersectionObserver：測試用 trigger() 模擬哨兵進入視窗。
    context.IntersectionObserver = function (callback) {
      trigger = () => callback([{isIntersecting: true}]);
      this.observe = () => {};
      this.disconnect = () => {};
    };
  }
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui/infinite-scroll.js'), 'utf8'), context, {filename: 'infinite-scroll.js'});
  // 與 HTML 相同的載入順序；各檔共用同一個全域作用域。
  for (const name of ['app-utils.js', 'app.js', 'app-bulk.js', 'app-detail.js', 'app-create.js', 'app-init.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/web_static', name), 'utf8'), context, {filename: name});
  }
  await new Promise(resolve => setImmediate(resolve));
  // Rendering itself is unchanged; tests exercise the actual event and request state logic.
  vm.runInContext('renderGrid = () => {};', context);
  return {
    node, run: code => vm.runInContext(code, context), fetch: fn => { handler = fn; }, trigger: () => trigger(),
    context,
    key(event) {
      const full = {preventDefault() { this.prevented = true; }, target: {tagName: 'BODY'}, ...event};
      (listeners.keydown || []).forEach(fn => fn(full));
      return full;
    },
  };
}

const entryResponse = key => ({ok: true, json: async () => ({key, tags: [], manual_tags: [], tag_suggestions: []})});

test('Ctrl+Enter 和 Ctrl+S 只送出目前的表單，忽略組字、長按與普通 Enter', async () => {
  const h = await harness();
  const submitted = [];
  let dialog = null;
  h.context.document.querySelector = s => s === 'dialog[open]' ? dialog : h.node(s);
  for (const id of ['#editForm', '#createForm']) h.node(id).requestSubmit = () => submitted.push(id);
  h.run('state.selected = {key:"a"}');
  const enter = options => h.key({key:'Enter', ctrlKey:true, stopPropagation() {}, ...options});
  enter({ctrlKey:false});
  enter({isComposing:true});
  enter({repeat:true});
  enter({});
  enter({key:"s"});
  dialog = h.node('#createDialog');
  enter({});
  enter({key:"S"});
  dialog = h.node('#publicationDialog');
  enter({});
  assert.deepEqual(submitted, ['#editForm', '#editForm', '#createForm', '#createForm']);
});

test('新增梗圖的拖入與貼上共用選檔流程，文字貼上與無效檔案保留原狀', async () => {
  const h = await harness();
  const input = h.node('#createImage');
  const handlers = h.node('#createDialog').handlers;
  const changes = [];
  input.accept = 'image/png,image/jpeg,image/gif,image/webp';
  input.dispatchEvent = event => changes.push(event.type);
  h.context.Event = Event;
  h.context.DataTransfer = class {
    files = [];
    items = {add: file => this.files.push(file)};
  };
  const png = {type: 'image/png'};
  const gif = {type: 'image/gif'};
  let prevented = 0;
  const preventDefault = () => { prevented++; };
  handlers.drop({dataTransfer: {files: [png]}, preventDefault});
  assert.equal(input.files[0], png);
  handlers.paste({clipboardData: {files: [gif]}, preventDefault});
  handlers.paste({clipboardData: {files: []}, preventDefault});
  handlers.drop({dataTransfer: {files: [png, gif]}, preventDefault});
  assert.equal(input.files[0], gif);
  assert.deepEqual(changes, ['change', 'change']);
  assert.equal(prevented, 3);
});

// 讓 state 處於「已載入 items、正在檢視第一筆」的狀態。
async function openedHarness(keys, total = keys.length) {
  const h = await harness();
  h.run(`state.items = ${JSON.stringify(keys.map(key => ({key})))}; state.total = ${total};`);
  h.fetch(async url => entryResponse(decodeURIComponent(url.split('/').pop())));
  await h.run(`openEntry(${JSON.stringify(keys[0])})`);
  return h;
}

test('favorite keeps all draft fields and ignores double clicks', async () => {
  const h = await harness();
  h.run('state.selected = {key: "first", favorite: false}');
  h.node('#drawerBody').innerHTML = 'unsaved title, quote, tags and notes';
  const pending = deferred();
  const calls = [];
  h.fetch(async (url, options) => {
    calls.push([url, options]);
    if (url.endsWith('/favorite')) return pending.promise;
    return {ok: true, json: async () => ({items: [], total: 0, page: 1})};
  });
  const click = h.node('#favoriteButton').handlers.click;
  const first = click();
  await click();
  assert.equal(calls.length, 1);
  pending.resolve({ok: true, json: async () => ({favorite: true})});
  await first;
  assert.equal(h.node('#drawerBody').innerHTML, 'unsaved title, quote, tags and notes');
  assert.equal(h.node('#favoriteButton').textContent, '★');
  assert.equal(h.run('state.selected.favorite'), true);
  assert.equal(h.node('#favoriteButton').disabled, false);
  assert.ok(calls.every(([url]) => url.endsWith('/favorite') || url.startsWith('/api/entries?')));
});

test('favorite response cannot replace a newly selected entry', async () => {
  const h = await harness();
  h.run('state.selected = {key: "first", favorite: false}');
  const pending = deferred();
  h.fetch(async url => url.endsWith('/favorite') ? pending.promise
    : {ok: true, json: async () => ({items: [], total: 0, page: 1})});
  const result = h.node('#favoriteButton').handlers.click();
  h.run('state.selected = {key: "second", favorite: false}');
  h.node('#favoriteButton').textContent = '☆';
  h.node('#drawerBody').innerHTML = 'second draft';
  pending.resolve({ok: true, json: async () => ({favorite: true})});
  await result;
  assert.equal(h.run('state.selected.key'), 'second');
  assert.equal(h.run('state.selected.favorite'), false);
  assert.equal(h.node('#favoriteButton').textContent, '☆');
  assert.equal(h.node('#drawerBody').innerHTML, 'second draft');
});

test('late old filter response is discarded; random seed stays stable across pages', async () => {
  const h = await harness();
  const calls = [];
  h.fetch(url => {
    const pending = deferred();
    calls.push({url, pending});
    return pending.promise;
  });
  h.node('#sortFilter').value = 'random';
  const old = h.run('loadEntries(true)');
  h.node('#searchInput').value = 'new';
  const current = h.run('loadEntries(true)');
  calls[1].pending.resolve({ok: true, json: async () => ({items: [{key: 'new'}], total: 3, page: 1})});
  await current;
  calls[0].pending.resolve({ok: true, json: async () => ({items: [{key: 'old'}], total: 9, page: 1})});
  await old;
  assert.equal(h.run('state.items[0].key'), 'new');
  const more = h.run('loadEntries(false)');
  await h.run('loadEntries(false)');
  assert.equal(calls.length, 3);
  const firstQuery = new URLSearchParams(calls[1].url.split('?')[1]);
  const moreQuery = new URLSearchParams(calls[2].url.split('?')[1]);
  assert.equal(firstQuery.get('seed'), moreQuery.get('seed'));
  assert.equal(moreQuery.get('page'), '2');
  calls[2].pending.resolve({ok: true, json: async () => ({items: [{key: 'next'}], total: 3, page: 2})});
  await more;
  assert.equal(h.run('state.items.map(item => item.key).join(",")'), 'new,next');
  assert.equal(h.run('state.page'), 2);
});

test('failed page request can retry the same page', async () => {
  const h = await harness();
  const urls = [];
  h.fetch(async url => { urls.push(url); throw new Error('network'); });
  await h.run('loadEntries(false)');
  await h.run('loadEntries(false)');
  assert.equal(urls[0], urls[1]);
  assert.equal(h.run('state.page'), 1);
  assert.equal(h.node('#loadMore').disabled, false);
});

test('search scope sends field only when there is a query', async () => {
  const h = await harness();
  const urls = [];
  h.fetch(async url => { urls.push(url); return {ok: true, json: async () => ({items: [], total: 0, page: 1})}; });
  h.node('#searchScope').handlers.change({target: {value: 'work'}});
  assert.equal(h.node('#searchInput').placeholder, '搜尋作品名稱');
  assert.equal(urls.length, 0);
  await h.run('loadEntries(true)');
  assert.equal(new URLSearchParams(urls[0].split('?')[1]).has('field'), false);
  assert.equal(h.run('"field" in currentFilter()'), false);
  h.node('#searchInput').value = '鬼滅';
  await h.run('loadEntries(true)');
  assert.equal(new URLSearchParams(urls[1].split('?')[1]).get('field'), 'work');
  assert.equal(h.run('currentFilter().field'), 'work');
  h.node('#searchScope').handlers.change({target: {value: ''}});
  assert.equal(h.run('"field" in currentFilter()'), false);
});

test('pending suggestions filter sends the flag and widens ready to all', async () => {
  const h = await harness();
  const urls = [];
  h.fetch(async url => { urls.push(url); return {ok: true, json: async () => ({items: [], total: 0, page: 1})}; });
  h.node('#statusFilter').value = 'ready';
  h.node('#pendingSuggestions').checked = true;
  await h.node('#pendingSuggestions').handlers.change();
  const query = new URLSearchParams(urls.at(-1).split('?')[1]);
  assert.equal(query.get('pending_suggestions'), '1');
  assert.equal(query.get('status'), 'all');
  assert.equal(h.run('currentFilter().pending_suggestions'), '1');
  // 已經選了其他狀態就不改。
  h.node('#statusFilter').value = 'ignored';
  await h.node('#pendingSuggestions').handlers.change();
  assert.equal(h.node('#statusFilter').value, 'ignored');
});

test('needs-review chip and status select stay in sync; counts come from meta', async () => {
  const h = await harness();
  const urls = [];
  h.fetch(async url => { urls.push(url); return {ok: true, json: async () => ({items: [], total: 0, page: 1})}; });
  h.node('#needsReviewOnly').checked = true;
  await h.node('#needsReviewOnly').handlers.change();
  assert.equal(h.node('#statusFilter').value, 'needs_review');
  assert.equal(new URLSearchParams(urls.at(-1).split('?')[1]).get('status'), 'needs_review');
  h.node('#needsReviewOnly').checked = false;
  await h.node('#needsReviewOnly').handlers.change();
  assert.equal(h.node('#statusFilter').value, 'ready');
  h.node('#statusFilter').value = 'needs_review';
  h.node('#statusFilter').handlers.change();
  assert.equal(h.node('#needsReviewOnly').checked, true);
  h.node('#statusFilter').value = 'all';
  h.node('#statusFilter').handlers.change();
  assert.equal(h.node('#needsReviewOnly').checked, false);
  h.fetch(async () => ({ok: true, json: async () => ({tags: [], statuses: [{status: 'needs_review', count: 7}], pending_suggestions: 3})}));
  await h.run('loadMeta()');
  assert.equal(h.node('#needsReviewCount').textContent, '7');
  assert.equal(h.node('#pendingSuggestionsCount').textContent, '3');
  assert.equal(h.node('#pendingSuggestionsCount').hidden, false);
});

test('infinite scroll loads the next page and does not retry after a failure', async () => {
  const h = await harness({observer: true});
  const urls = [];
  const page = (n, total) => ({ok: true, json: async () => ({items: [{key: `k${n}`}], total, page: n})});
  h.fetch(async url => { urls.push(url); return page(Number(new URLSearchParams(url.split('?')[1]).get('page')), 4); });
  await h.run('loadEntries(true)');
  assert.equal(h.node('#loadMore').hidden, true);
  await h.trigger();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(new URLSearchParams(urls.at(-1).split('?')[1]).get('page'), '2');
  const before = urls.length;
  h.fetch(async url => { urls.push(url); throw new Error('network'); });
  await h.trigger();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(urls.length, before + 1);
  assert.equal(h.run('state.loadFailed'), true);
  assert.equal(h.node('#loadMore').hidden, false);
  assert.equal(h.node('#loadMore').textContent, '重試');
  await h.trigger();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(urls.length, before + 1);
  // 按下重試後恢復載入。
  h.fetch(async url => { urls.push(url); return page(3, 4); });
  await h.node('#loadMore').handlers.click();
  assert.equal(h.run('state.loadFailed'), false);
  assert.equal(h.run('state.items.length'), 3);
  assert.equal(h.run('state.page'), 3);
});

test('errorMessage reads both the string and the {code, message} error formats', async () => {
  const h = await harness();
  assert.equal(h.run('errorMessage({error: "舊格式"}, "預設")'), '舊格式');
  assert.equal(h.run('errorMessage({error: {code: "bad", message: "新格式"}}, "預設")'), '新格式');
  assert.equal(h.run('errorMessage({}, "預設")'), '預設');
  assert.equal(h.run('errorMessage(null, "預設")'), '預設');
  assert.equal(h.run('errorMessage({error: {code: "x"}}, "預設")'), '預設');
});

test('openEntry discards a slow response that is no longer the latest', async () => {
  const h = await harness();
  const pending = {};
  h.fetch(url => { pending[url.split('/').pop()] = deferred(); return pending[url.split('/').pop()].promise; });
  const a = h.run('openEntry("A")');
  const b = h.run('openEntry("B")');
  pending.B.resolve(entryResponse('B'));
  await b;
  pending.A.resolve(entryResponse('A'));
  await a;
  assert.equal(h.run('state.selected.key'), 'B');
});

test('openEntry network failure shows a toast and does not reject', async () => {
  const h = await harness();
  h.fetch(async () => { throw new Error('network'); });
  await h.run('openEntry("A")');
  assert.equal(h.node('#toast').textContent, '詳情載入失敗，請重試');
  assert.equal(h.run('state.selected'), null);
});

test('ArrowRight moves to the next entry; ignored in inputs and with modifiers', async () => {
  const h = await openedHarness(['a', 'b', 'c']);
  assert.equal(h.node('#prevEntry').disabled, true);
  assert.equal(h.node('#entryPosition').textContent, '1 / 3');
  h.key({key: 'ArrowRight', target: {tagName: 'INPUT'}});
  h.key({key: 'ArrowRight', target: {tagName: 'TEXTAREA'}});
  h.key({key: 'ArrowRight', ctrlKey: true});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.run('state.selected.key'), 'a');
  const event = h.key({key: 'ArrowRight'});
  assert.equal(event.prevented, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.run('state.selected.key'), 'b');
  assert.equal(h.node('#entryPosition').textContent, '2 / 3');
  assert.equal(h.node('#prevEntry').disabled, false);
  h.key({key: 'ArrowLeft'});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.run('state.selected.key'), 'a');
});

test('stepping past the last loaded entry loads the next page first; end of list disables next', async () => {
  const h = await openedHarness(['a', 'b'], 3);
  await h.run('stepEntry(1)');
  assert.equal(h.run('state.selected.key'), 'b');
  assert.equal(h.node('#nextEntry').disabled, false);
  const urls = [];
  h.fetch(async url => {
    urls.push(url);
    if (url.startsWith('/api/entries?')) return {ok: true, json: async () => ({items: [{key: 'c'}], total: 3, page: 2})};
    return entryResponse(decodeURIComponent(url.split('/').pop()));
  });
  await h.run('stepEntry(1)');
  assert.ok(urls[0].startsWith('/api/entries?'));
  assert.equal(new URLSearchParams(urls[0].split('?')[1]).get('page'), '2');
  assert.equal(h.run('state.selected.key'), 'c');
  assert.equal(h.node('#nextEntry').disabled, true);
  await h.run('stepEntry(1)');
  assert.equal(h.run('state.selected.key'), 'c');
});

test('unsaved edits trigger confirm; declining keeps the current entry', async () => {
  const h = await harness();
  const field = {name: 'quote_text', value: 'original'};
  h.node('#editForm').elements = [field];
  h.run('state.items = [{key: "a"}, {key: "b"}]; state.total = 2;');
  h.fetch(async url => entryResponse(decodeURIComponent(url.split('/').pop())));
  await h.run('openEntry("a")');
  let asked = 0;
  h.context.confirm = () => { asked += 1; return false; };
  await h.run('stepEntry(1)');
  assert.equal(asked, 0);
  assert.equal(h.run('state.selected.key'), 'b');
  await h.run('stepEntry(-1)');
  assert.equal(h.run('state.selected.key'), 'a');
  field.value = 'edited';
  await h.run('stepEntry(1)');
  assert.equal(asked, 1);
  assert.equal(h.run('state.selected.key'), 'a');
  h.context.confirm = () => { asked += 1; return true; };
  await h.run('stepEntry(1)');
  assert.equal(asked, 2);
  assert.equal(h.run('state.selected.key'), 'b');
});

const tick = () => new Promise(resolve => setImmediate(resolve));

// 審核候選標籤時列表不重載：fetch 內記錄所有請求。
async function reviewHarness(keys) {
  const h = await harness();
  const urls = [];
  h.run(`state.items = ${JSON.stringify(keys.map(key => ({key})))}; state.total = ${keys.length};`);
  h.node('#pendingSuggestions').checked = true;
  h.fetch(async (url, options) => {
    urls.push(url);
    if (url === '/api/meta') return {ok: true, json: async () => ({tags: []})};
    if (url.includes('/decision')) return {ok: true, json: async () => ({id: 1, status: 'accepted', label: 'x'})};
    const key = decodeURIComponent(url.split('/').pop());
    return {ok: true, json: async () => ({key, tags: [], manual_tags: [], tag_suggestions: key === 'a' && !(options && options.method) && url.includes('fresh') ? [{id: 1}] : []})};
  });
  return {h, urls};
}

test('accepting the last suggestion keeps the list, marks processed, and arrows still work', async () => {
  const {h, urls} = await reviewHarness(['a', 'b', 'c']);
  h.fetch(async url => {
    urls.push(url);
    if (url === '/api/meta') return {ok: true, json: async () => ({tags: []})};
    if (url.includes('/decision')) return {ok: true, json: async () => ({id: 1, status: 'accepted', label: 'x'})};
    const key = decodeURIComponent(url.split('/').pop());
    return {ok: true, json: async () => ({key, tags: [], manual_tags: [], tag_suggestions: []})};
  });
  h.run(`state.selected = {key: 'a', tags: [], tag_suggestions: [{id: 1}]};`);
  // 直接走決策後的同步流程：重新取得該筆並更新列表。
  const fresh = await h.run('fetchEntry("a")');
  h.run('syncEntry(' + JSON.stringify(fresh) + ')');
  assert.equal(h.run('state.items.length'), 3);
  assert.equal(h.run('state.processed.has("a")'), true);
  assert.equal(h.node('#refreshList').hidden, false);
  await h.run('stepEntry(1)');
  assert.equal(h.run('state.selected.key'), 'b');
  assert.ok(urls.every(url => !url.startsWith('/api/entries?')));
});

test('decision button handler does not reload the list', async () => {
  const h = await harness();
  const urls = [];
  const button = {
    dataset: {suggestionId: '1', decision: 'accepted'}, disabled: false, handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    closest: () => ({isConnected: false, querySelectorAll: () => [button]}),
  };
  h.context.document.querySelectorAll = selector => (selector === '[data-suggestion-id]' ? [button] : []);
  h.run('state.items = [{key: "a"}, {key: "b"}]; state.total = 2;');
  h.node('#pendingSuggestions').checked = true;
  h.fetch(async url => {
    urls.push(url);
    if (url === '/api/meta') return {ok: true, json: async () => ({tags: []})};
    if (url.includes('/decision')) return {ok: true, json: async () => ({id: 1, status: 'accepted', label: 'x'})};
    return {ok: true, json: async () => ({key: 'a', tags: [], manual_tags: [], tag_suggestions: []})};
  });
  h.run('bindDetailForm({key: "a", tags: [], tag_suggestions: [{id: 1}]})');
  await button.handlers.click();
  assert.equal(h.run('state.items.length'), 2);
  assert.equal(h.run('state.processed.has("a")'), true);
  assert.ok(urls.every(url => !url.startsWith('/api/entries?')));
  assert.ok(urls.includes('/api/entries/a'));
});

test('saving does not reload the list and refreshes the form snapshot', async () => {
  const h = await harness();
  const urls = [];
  const form = {
    dataset: {}, display_title: {value: '梗圖'}, search_aliases: {value: ' 一句 話\n\n別名 '},
    anime_title: {value: 't'}, source_page: {value: ''}, speaker: {value: ''}, quote_text: {value: 'q'},
    status: {value: 'ready'}, notes: {value: ''}, tags: {value: 'x'}, elements: [{name: 'quote_text', value: 'q'}],
    handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; },
  };
  h.context.document.querySelector = selector => (selector === '#editForm' ? form : selector === 'dialog[open]' ? null : h.node(selector));
  h.run('state.items = [{key: "a"}]; state.total = 1;');
  h.fetch(async (url, options) => {
    urls.push(url);
    if (options?.method === 'PATCH') {
      const data = JSON.parse(options.body);
      assert.equal(data.display_title, '梗圖');
      assert.deepEqual(data.search_aliases, ['一句 話', '別名']);
      assert.equal(data.tags, 'x');
    }
    if (url === '/api/meta') return {ok: true, json: async () => ({tags: []})};
    return {ok: true, json: async () => ({key: 'a', tags: [], manual_tags: [], tag_suggestions: []})};
  });
  h.run('state.selected = {key: "a"}');
  h.run('bindDetailForm({key: "a", tags: [], tag_suggestions: []})');
  form.elements[0].value = 'edited';
  await form.handlers.submit({preventDefault() {}});
  assert.deepEqual(urls, ['/api/entries/a', '/api/entries/a', '/api/meta']);
  assert.ok(urls.every(url => !url.startsWith('/api/entries?')));
  assert.equal(h.run('hasUnsavedChanges()'), false);
});

test('關閉鈕、背景、Esc 取消關閉都保留草稿', async () => {
  const h = await openedHarness(['a']);
  const field = {name: 'quote_text', value: '草稿'};
  h.node('#editForm').elements = [field];
  let calls = 0;
  h.context.confirm = () => { calls++; return false; };
  h.node('#closeDrawer').handlers.click();
  h.node('#scrim').handlers.click();
  h.key({key: 'Escape'});
  assert.equal(calls, 3);
  assert.equal(h.run('state.selected.key'), 'a');
  assert.equal(field.value, '草稿');
  h.context.confirm = () => true;
  h.key({key: 'Escape'});
  assert.equal(h.run('state.selected'), null);
});

test('自動搜尋沿用後端排序，手動排序優先', async () => {
  const h = await harness();
  h.node('#searchInput').value = '別名';
  assert.equal(h.run('params().get("sort")'), null);
  h.node('#sortFilter').value = 'updated';
  assert.equal(h.run('params().get("sort")'), 'updated');
  h.node('#sortFilter').value = 'relevance';
  assert.equal(h.run('params().get("sort")'), 'relevance');
});

test('deleting opens the next entry at the same position and lowers total', async () => {
  const h = await harness();
  h.node('#deleteEntry');
  const del = {handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; }};
  const form = {elements: [], addEventListener() {}};
  h.context.document.querySelector = selector => (selector === '#deleteEntry' ? del : selector === '#editForm' ? form : selector === 'dialog[open]' ? null : h.node(selector));
  h.run('state.items = [{key: "a"}, {key: "b"}, {key: "c"}]; state.total = 3;');
  const urls = [];
  h.fetch(async (url, options) => {
    urls.push(url);
    if (url === '/api/meta') return {ok: true, json: async () => ({tags: []})};
    if (options && options.method === 'DELETE') return {ok: true, json: async () => ({})};
    return entryResponse(decodeURIComponent(url.split('/').pop()));
  });
  h.run('state.selected = {key: "b"}');
  h.run('bindDetailForm({key: "b", tags: [], tag_suggestions: []})');
  await del.handlers.click();
  assert.equal(h.run('state.items.map(i => i.key).join(",")'), 'a,c');
  assert.equal(h.run('state.total'), 2);
  assert.equal(h.run('state.selected.key'), 'c');
  assert.ok(urls.every(url => !url.startsWith('/api/entries?')));
});

test('showToast cancels the previous timer and scales duration with length', async () => {
  const h = await harness();
  const cleared = [];
  const delays = [];
  let next = 10;
  h.context.setTimeout = (fn, delay) => { delays.push(delay); return ++next; };
  h.context.clearTimeout = id => cleared.push(id);
  h.run('showToast("短")');
  h.run('showToast("x".repeat(200))');
  assert.deepEqual(cleared.slice(-1), [11]);
  assert.equal(delays[0], 3040);
  assert.equal(delays[1], 6000);
});
