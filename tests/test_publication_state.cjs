const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const nodes = new Map();
  function node(key) {
    if (!nodes.has(key)) nodes.set(key, {
      value: '', checked: false, disabled: false, hidden: false, textContent: '', open: false,
      children: [], handlers: {},
      replaceChildren(...children) { this.children = children; if (key === '#vpsVersion') this.value = ''; },
      append(...children) {
        this.children.push(...children);
        if (key === '#vpsVersion' && !this.value) this.value = children[0].value;
      },
      removeAttribute() {},
      addEventListener(event, handler) { this.handlers[event] = handler; },
      showModal() { this.open = true; }, close() { this.open = false; this.handlers.close?.(); }
    });
    return nodes.get(key);
  }
  const fields = [node('ssh_host'), node('release_root'), node('save')];
  fields.ssh_host = fields[0]; fields.release_root = fields[1];
  node('#vpsSettingsForm').elements = fields;
  let state = {config: {ssh_host: 'test.invalid', release_root: '/srv/releases'}, ssh_available: true,
    ssh_ready: true, versions: [{version: 'v2', count: 10}, {version: 'v1', count: 9}], job: null};
  const calls = [], timers = new Map();
  let serial = 0, pendingGet = null;
  const context = vm.createContext({
    document: {querySelector: node, createElement: () => node(Symbol())},
    setTimeout: callback => { timers.set(++serial, callback); return serial; },
    clearTimeout: id => timers.delete(id), confirm: () => true,
    errorMessage: (_, fallback) => fallback,
    fetch: async (url, options) => {
      calls.push({url, options});
      if (options.method === 'GET' && pendingGet) return pendingGet;
      if (options.method === 'POST' && url === '/api/publication/vps') {
        state.job = {...JSON.parse(options.body), target: 'test.invalid', status: 'running', stage: '上傳發布包', events: []};
        return {ok: true, json: async () => structuredClone(state.job)};
      }
      return {ok: true, json: async () => structuredClone(state)};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/web_static/publication.js'), 'utf8'), context);
  return {node, calls, timers, setState: value => state = {...state, ...value},
    setPendingGet: value => pendingGet = value,
    refresh: () => vm.runInContext('refreshVpsPublication()', context)};
}

test('未設定 SSH 時禁止推送，保留既有本機流程', async () => {
  const h = harness();
  h.setState({ssh_ready: false});
  await h.refresh();
  assert.equal(h.node('#publishVps').disabled, true);
  assert.match(h.node('#vpsStatus').textContent, /金鑰或主機指紋/);
  assert.equal(typeof h.node('#exportPublication').onclick, 'function');
});

test('推送傳送版本與啟用選項，執行中禁止再次送出', async () => {
  const h = harness();
  h.node('#publicationDialog').open = true;
  await h.refresh();
  h.node('#vpsActivate').checked = true;
  await h.node('#publishVps').onclick();
  const posts = h.calls.filter(call => call.options.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].options.body), {version: 'v2', activate: true});
  assert.equal(h.node('#publishVps').disabled, true);
  assert.equal(h.node('#vpsProgress').hidden, false);
  await h.node('#publishVps').onclick();
  assert.equal(h.calls.filter(call => call.options.method === 'POST').length, 1);
  assert.equal(h.timers.size, 1);
  h.node('#publicationDialog').close();
  assert.equal(h.timers.size, 0);
});

test('重新開啟可恢復完成結果與增量傳輸摘要', async () => {
  const h = harness();
  h.setState({job: {status: 'succeeded', stage: 'VPS 已啟用此版', target: 'test.invalid', version: 'v2',
    bundle: {media: 2, media_reused: 8, bytes: 1048576}, events: [{time: '2026-11-26T12:00:00Z', message: '完成'}]}});
  await h.refresh();
  assert.match(h.node('#vpsStatus').textContent, /完成.*傳送 2 張縮圖、沿用 8 張/);
  assert.equal(h.node('#vpsProgress').hidden, true);
  assert.equal(h.node('#vpsEvents').children.length, 1);
});

test('不重疊輪詢，查詢失敗後禁止盲目重送', async () => {
  const h = harness();
  let resolve;
  h.setPendingGet(new Promise(done => resolve = done));
  const first = h.refresh();
  await h.refresh();
  assert.equal(h.calls.length, 1);
  resolve({ok: false, status: 503, json: async () => ({})});
  await first;
  assert.equal(h.node('#publishVps').disabled, true);
  assert.match(h.node('#vpsStatus').textContent, /請勿重複推送/);
});
