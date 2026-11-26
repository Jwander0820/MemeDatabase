(() => {
  'use strict';
  const data = globalThis.MemePersonalData;
  const get = id => document.getElementById(id);
  const editor = get('personalEditor'), form = get('personalForm'), imageInput = get('personalImage');
  const status = get('personalStatus'), error = get('personalError'), mail = get('personalMail');
  const make = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
  let records = [], visible = 24, editing = null, selectedFile = null, previewUrl = '', mailUrl = '';
  let cardUrls = [], busy = false, dirty = false, imageRequest = 0, invalidImage = false, readingImage = false;
  const buttons = () => Array.from(form.querySelectorAll('button'));

  function showCollection(personal) {
    get('publicContent').hidden = personal;
    get('personalContent').hidden = !personal;
    get('filters').hidden = get('tagStrip').hidden = personal;
    get('personalCollection').setAttribute('aria-pressed', String(personal));
    if (personal) refresh();
  }
  get('publicCollection').onclick = () => showCollection(false);
  get('personalCollection').onclick = () => showCollection(get('personalContent').hidden);

  function download(blob, name) {
    const url = URL.createObjectURL(blob), link = make('a');
    link.href = url; link.download = name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  function preview(blob) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    get('personalPreview').src = previewUrl;
    imageInput.closest('.upload-field').classList.add('has-preview');
  }
  function openEditor(record = null) {
    ++imageRequest; editing = record; selectedFile = null; busy = dirty = invalidImage = readingImage = false;
    form.reset(); error.textContent = ''; buttons().forEach(button => { button.disabled = false; });
    imageInput.required = !record;
    get('personalEditorTitle').textContent = record ? '編輯自己的梗圖' : '新增自己的梗圖';
    for (const key of Object.keys(data.fields({}))) form.elements[key].value = record?.fields[key] || '';
    get('personalPreview').removeAttribute('src'); imageInput.closest('.upload-field').classList.remove('has-preview');
    if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = '';
    if (record) preview(record.image);
    editor.showModal();
  }
  function mayClose() { return !busy && (!dirty || confirm('尚未儲存，確定放棄這次修改？')); }
  get('addPersonal').onclick = () => openEditor();
  for (const id of ['closePersonal', 'cancelPersonal']) get(id).onclick = () => { if (mayClose()) editor.close(); };
  editor.addEventListener('cancel', event => { if (!mayClose()) event.preventDefault(); });
  editor.addEventListener('close', () => {
    if (editor.open) return;
    ++imageRequest;
    if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = '';
    selectedFile = editing = null;
  });
  form.addEventListener('input', () => { dirty = true; });

  async function selectFile(files) {
    if (busy) return;
    const request = ++imageRequest;
    if (files.length !== 1) { error.textContent = '請一次選擇一張圖片'; return; }
    dirty = true; readingImage = true; invalidImage = false;
    try {
      const blob = await data.image(files[0]);
      if (request !== imageRequest) return;
      selectedFile = blob; imageInput.required = false; preview(blob); error.textContent = '';
    } catch (exception) {
      if (request === imageRequest) { invalidImage = true; error.textContent = exception.message; }
    } finally { if (request === imageRequest) readingImage = false; }
  }
  imageInput.onchange = () => { if (imageInput.files.length) selectFile(imageInput.files); };
  editor.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  editor.addEventListener('drop', event => { if (event.dataTransfer.files.length) { event.preventDefault(); selectFile(event.dataTransfer.files); } });
  editor.addEventListener('paste', event => {
    if (event.clipboardData?.files?.length) { event.preventDefault(); selectFile(event.clipboardData.files); }
  });

  function prepareMail(record) {
    const submission = data.submission(record);
    if (mailUrl) URL.revokeObjectURL(mailUrl);
    mailUrl = URL.createObjectURL(record.image);
    get('personalMailBody').value = submission.body;
    get('downloadPersonalMail').href = mailUrl; get('downloadPersonalMail').download = record.name;
    get('openPersonalMail').href = submission.href;
    get('personalMailStatus').textContent = submission.truncated
      ? '內容較長，請先複製全文再貼到信件。尚未寄出，圖片需自行附上。'
      : '尚未寄出。請在郵件 App 附上圖片後自行送出；若無郵件 App，可手動寄信至 meme@jwander.net。';
    mail.showModal();
  }
  mail.addEventListener('close', () => { if (mailUrl) URL.revokeObjectURL(mailUrl); mailUrl = ''; });
  get('copyPersonalMail').onclick = async () => {
    try {
      await navigator.clipboard.writeText(get('personalMailBody').value);
      get('personalMailStatus').textContent = '已複製投稿內容；請另外附上圖片，這封信尚未寄出。';
    } catch {
      get('personalMailBody').select();
      get('personalMailStatus').textContent = '請手動複製已選取的內容，並在郵件 App 貼上與附圖。';
    }
  };

  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    if (readingImage || invalidImage) { error.textContent = '請等待圖片驗證完成，或重新選擇有效圖片'; return; }
    const blob = selectedFile || editing?.image;
    if (!blob) { error.textContent = '請先選擇圖片'; return; }
    const values = Object.fromEntries(Object.keys(data.fields({})).map(key => [key, form.elements[key].value]));
    const submit = event.submitter?.value === 'submit';
    busy = true; Array.from(form.elements).forEach(control => { control.disabled = true; }); error.textContent = '正在儲存至此瀏覽器…';
    try {
      const record = await data.save(values, blob, editing);
      dirty = false; editor.close(); showCollection(true);
      if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
      if (submit) prepareMail(record);
    } catch (exception) { error.textContent = exception.message; }
    finally { busy = false; Array.from(form.elements).forEach(control => { control.disabled = false; }); }
  });

  function render() {
    cardUrls.forEach(url => URL.revokeObjectURL(url)); cardUrls = [];
    const query = get('personalQuery').value.trim().normalize('NFKC').toLocaleLowerCase();
    const terms = query.split(/\s+/).filter(Boolean);
    const filtered = records.filter(record => {
      const text = Object.values(record.fields).join(' ').normalize('NFKC').toLocaleLowerCase();
      return terms.every(term => text.includes(term));
    });
    const target = get('personalResults'); target.replaceChildren();
    for (const record of filtered.slice(0, visible)) {
      const node = make('article'); node.className = 'personal-card';
      const img = make('img'); img.alt = record.fields.display_title || '自己的梗圖'; img.loading = 'lazy';
      img.src = URL.createObjectURL(record.image); cardUrls.push(img.src);
      const title = make('h3', record.fields.display_title || record.fields.quote_text || record.fields.anime_title || '未命名梗圖');
      const details = make('p', [record.fields.anime_title, record.fields.speaker, record.fields.tags].filter(Boolean).join(' · '));
      const actions = make('div'); actions.className = 'personal-tools';
      for (const [text, action] of [
        ['編輯', () => openEditor(record)], ['下載圖片', () => download(record.image, record.name)],
        ['準備投稿', () => prepareMail(record)], ['刪除', async () => {
          if (!confirm('確定刪除此瀏覽器中的這張梗圖？建議先匯出備份。')) return;
          try { await data.remove(record.id); await refresh(); } catch (exception) { status.textContent = exception.message; }
        }]
      ]) { const button = make('button', text); button.type = 'button'; button.onclick = action; actions.append(button); }
      node.append(img, title, details, actions); target.append(node);
    }
    get('personalMore').hidden = filtered.length <= visible;
    get('exportPersonal').disabled = !records.length;
    status.textContent = records.length
      ? `此瀏覽器共 ${records.length} 張；顯示 ${Math.min(visible, filtered.length)}／${filtered.length} 張符合的梗圖。`
      : '此瀏覽器尚無自己的梗圖，可新增圖片或匯入備份。';
  }
  async function refresh() {
    try {
      records = (await data.all()).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)); render();
    } catch (exception) { status.textContent = exception.message; }
  }
  get('personalQuery').oninput = () => { visible = 24; render(); };
  get('personalMore').onclick = () => { visible += 24; render(); };
  get('exportPersonal').onclick = async () => {
    get('exportPersonal').disabled = true; status.textContent = '正在產生本地備份…';
    try {
      const blob = await data.encode(await data.all());
      download(blob, 'memedatabase-personal-v1-' + new Date().toISOString().slice(0, 10) + '.json');
      status.textContent = '已開始下載備份，請確認檔案已保存；圖片未上傳到本站。';
    } catch (exception) { status.textContent = exception.message; }
    finally { get('exportPersonal').disabled = !records.length; }
  };
  get('importPersonal').onclick = () => get('personalBackup').click();
  get('personalBackup').onchange = async () => {
    const file = get('personalBackup').files[0]; if (!file) return;
    get('importPersonal').disabled = true; status.textContent = '正在檢查備份格式與圖片…';
    try {
      const result = await data.importBackup(file);
      await refresh(); status.textContent = `已匯入 ${result.added} 張，略過 ${result.skipped} 張相同編號；不覆寫既有資料。`;
    } catch (exception) { status.textContent = exception.message; }
    finally { get('importPersonal').disabled = false; get('personalBackup').value = ''; }
  };
})();
