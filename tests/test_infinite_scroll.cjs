const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// 以假的 IntersectionObserver 與 requestAnimationFrame 執行共用的捲到底自動載入模組。
function harness() {
  let observerCallback = null;
  const frames = [];
  const context = vm.createContext({
    window: {},
    IntersectionObserver: class {
      constructor(callback) { observerCallback = callback; }
      observe() {}
      disconnect() { observerCallback = null; }
    },
    requestAnimationFrame: fn => frames.push(fn),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui/infinite-scroll.js'), 'utf8'), context);
  return {
    create: options => context.window.MemeInfiniteScroll.create({}, options),
    intersect: visible => observerCallback?.([{isIntersecting: visible}]),
    // 跑完目前排隊的動畫影格（每個影格可能再排下一個），並等待其中的非同步載入。
    async flush(limit = 20) {
      for (let i = 0; i < limit && frames.length; i++) {
        frames.shift()();
        await new Promise(resolve => setImmediate(resolve));
      }
    },
  };
}

test('哨兵可見時連續載入直到沒有更多', async () => {
  const h = harness();
  let pages = 1;
  const total = 4;
  h.create({canLoad: () => pages < total, onLoad: async () => { pages += 1; }});
  h.intersect(true);
  await new Promise(resolve => setImmediate(resolve));
  await h.flush();
  assert.equal(pages, total);
});

test('哨兵離開視窗後停止，回到視窗再繼續', async () => {
  const h = harness();
  let pages = 1;
  h.create({canLoad: () => pages < 10, onLoad: async () => { pages += 1; h.intersect(false); }});
  h.intersect(true);
  await new Promise(resolve => setImmediate(resolve));
  await h.flush();
  assert.equal(pages, 2);
  h.intersect(true);
  await new Promise(resolve => setImmediate(resolve));
  await h.flush();
  assert.equal(pages, 3);
});

test('載入失敗後 canLoad 為 false，不會自動重試', async () => {
  const h = harness();
  let calls = 0;
  let failed = false;
  h.create({canLoad: () => !failed, onLoad: async () => { calls += 1; failed = true; }});
  h.intersect(true);
  await new Promise(resolve => setImmediate(resolve));
  await h.flush();
  assert.equal(calls, 1);
});

test('載入進行中不會重複呼叫', async () => {
  const h = harness();
  let calls = 0;
  let release;
  const loader = h.create({canLoad: () => calls < 5, onLoad: () => { calls += 1; return new Promise(r => { release = r; }); }});
  h.intersect(true);
  h.intersect(true);
  loader.check();
  assert.equal(calls, 1);
  release();
});

test('不支援 IntersectionObserver 時回傳 null', () => {
  const context = vm.createContext({window: {}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui/infinite-scroll.js'), 'utf8'), context);
  assert.equal(context.window.MemeInfiniteScroll.create({}, {canLoad: () => true, onLoad: async () => {}}), null);
});
