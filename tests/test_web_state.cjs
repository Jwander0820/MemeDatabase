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

async function harness() {
  const nodes = new Map();
  function node(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: '', checked: false, disabled: false, innerHTML: '', textContent: '',
      handlers: {}, classList: {add() {}, remove() {}},
      addEventListener(type, handler) { this.handlers[type] = handler; },
      setAttribute() {}, appendChild() {},
    });
    return nodes.get(selector);
  }
  let handler = async url => ({ok: true, json: async () => url === '/api/meta'
    ? {tags: []} : {items: [], page: 1, total: 0}});
  const context = vm.createContext({
    document: {querySelector: node, createElement: () => node(Symbol()), querySelectorAll: () => []},
    fetch: (...args) => handler(...args), URLSearchParams, setTimeout: () => 0, clearTimeout() {},
  });
  // 與 HTML 相同的載入順序；各檔共用同一個全域作用域。
  for (const name of ['app-utils.js', 'app.js', 'app-bulk.js', 'app-detail.js', 'app-create.js', 'app-init.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/web_static', name), 'utf8'), context, {filename: name});
  }
  await new Promise(resolve => setImmediate(resolve));
  // Rendering itself is unchanged; tests exercise the actual event and request state logic.
  vm.runInContext('renderGrid = () => {};', context);
  return {node, run: code => vm.runInContext(code, context), fetch: fn => { handler = fn; }};
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
