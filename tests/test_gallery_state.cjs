const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function gallery() {
  const nodes = new Map();
  const pending = [];
  function element() {
    return {value: '', childElementCount: 0, addEventListener() {},
      replaceChildren() { this.childElementCount = 0; },
      append(...items) { this.childElementCount += items.length; },
      add() {}};
  }
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  }
  const context = vm.createContext({
    document: {getElementById: node, createElement: element},
    window: {addEventListener() {}}, location: {hash: ''},
    clearTimeout() {}, URLSearchParams, Option: class {},
    fetch(url) { return new Promise(resolve => pending.push({url, resolve})); }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui/gallery.js'), 'utf8'), context);
  function respond(request, data, status = 200) {
    request.resolve({ok: status === 200, status, json: async () => data});
    return new Promise(resolve => setImmediate(resolve));
  }
  return {node, pending, respond,
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
