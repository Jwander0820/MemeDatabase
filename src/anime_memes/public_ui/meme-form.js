/* 管理頁與瀏覽器個人館藏共用新增／編輯欄位，不包含任何資料寫入或上傳。 */
(() => {
  'use strict';
  const fields = [
    ['anime_title', '作品名稱', 'input', false],
    ['speaker', '說話者', 'input', false],
    ['quote_text', '圖片台詞', 'textarea', true, '輸入圖片中的主要台詞'],
    ['display_title', '梗圖名稱（選填）', 'input', true],
    ['search_aliases', '也可以這樣搜（選填，每行一個替代詞）', 'textarea', true],
    ['tags', '標籤', 'input', false, '以逗號分隔'],
    ['status', '狀態', 'select', false],
    ['source_page', '圖片出處（選填）', 'input', true, 'https://'],
    ['notes', '備註', 'textarea', true, '選填，例如圖片來源或待確認事項']
  ];
  function mount(target, {admin = false} = {}) {
    for (const [name, title, tag, wide, placeholder] of fields) {
      if (name === 'status' && !admin) continue;
      const label = document.createElement('label');
      label.className = 'field' + (wide ? ' create-wide' : '');
      const span = document.createElement('span'); span.textContent = title;
      const input = document.createElement(tag); input.name = name;
      if (tag === 'input') input.type = name === 'source_page' ? 'url' : 'text';
      if (placeholder) input.placeholder = placeholder;
      if (tag !== 'select') input.autocomplete = 'off';
      if (tag === 'select') {
        for (const [value, text] of [['ready', '可用'], ['needs_review', '待審核'], ['ignored', '已忽略']]) {
          const option = document.createElement('option'); option.value = value; option.textContent = text; input.append(option);
        }
      }
      label.append(span, input); target.append(label);
    }
  }
  globalThis.MemeForm = {mount, fields};
  document.querySelectorAll('[data-meme-fields]').forEach(target => mount(target, {admin: target.dataset.memeFields === 'admin'}));
})();
