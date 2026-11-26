const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = fs.readFileSync(path.join(__dirname, '../src/anime_memes/public_ui/logo-egg.js'), 'utf8');

function logo(storage) {
  let angle, click;
  vm.runInNewContext(script, {
    localStorage: storage,
    document: {
      documentElement: {style: {setProperty(name, value) { angle = value; }}},
      getElementById() { return {addEventListener(name, callback) { click = callback; }}; },
    },
    addEventListener(name, callback) { callback(); },
  });
  return {angle: () => angle, click: (timeStamp, extra = {}) => click({button: 0, detail: 1, timeStamp, ...extra})};
}

test('快速連點才觸發：首次轉正、每次加 15 度，重新開啟保留並順時針跨越整圈', () => {
  let saved = null;
  const storage = {getItem: () => saved, setItem(key, value) { saved = value; }};
  const first = logo(storage);
  assert.equal(first.angle(), '-4deg');
  for (let i = 0; i < 7; i++) first.click(i * 500);
  assert.equal(first.angle(), '-4deg', '點擊太慢不觸發');
  for (let i = 0; i < 7; i++) first.click(10000 + i * 100);
  assert.equal(first.angle(), '0deg');
  const reopened = logo(storage);
  assert.equal(reopened.angle(), '0deg');
  for (let round = 0; round < 24; round++) {
    for (let i = 0; i < 7; i++) reopened.click(20000 + round * 1000 + i * 100);
  }
  assert.equal(reopened.angle(), '360deg', '跨整圈仍順時針');
  assert.equal(logo(storage).angle(), '0deg');
});

test('忽略鍵盤與修飾鍵，儲存遭封鎖或內容異常時仍可使用彩蛋', () => {
  const invalid = logo({getItem: () => 'Infinity', setItem() {}});
  assert.equal(invalid.angle(), '-4deg');
  for (let i = 0; i < 7; i++) invalid.click(i * 100, {ctrlKey: true});
  for (let i = 0; i < 7; i++) invalid.click(i * 100, {detail: 0});
  assert.equal(invalid.angle(), '-4deg');
  const blocked = logo({getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }});
  for (let i = 0; i < 7; i++) blocked.click(i * 100);
  assert.equal(blocked.angle(), '0deg');
});
