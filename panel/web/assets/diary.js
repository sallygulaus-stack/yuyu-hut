/* The date, reading position and each unsaved edit belong to the mounted page. */
function 打开本地日记() {
  window.localDiary.open();
}

(() => {
  const byId = id => document.getElementById('journal' + id);
  const date = byId('Date'), dates = byId('Dates'), page = byId('Page');
  const body = byId('Body'), editor = byId('Editor'), text = byId('Text'), photos = byId('Photos');
  const edit = byId('Edit'), save = byId('Save'), refresh = byId('Refresh');
  const status = byId('Status');
  const entries = new Map();
  let current = '', savedDates = [], listing = 0, started = false, photoKey = '';

  function entry(day) {
    if (!entries.has(day)) entries.set(day, {body: '', revision: 0, draft: null,
      editing: false, ready: false, loading: false, saving: false,
      blocked: false, message: '', error: false, top: 0, writePos: 0, photos: []});
    return entries.get(day);
  }

  async function rpc(method, ...args) {
    const api = window.pywebview?.api;
    if (!api?.[method]) throw new Error();
    return api[method](...args);
  }

  function note(model, message, error = false) {
    model.message = message;
    model.error = error;
  }

  function draw() {
    const model = entry(current);
    byId('Day').textContent = current;
    edit.textContent = model.draft !== null ? '继续写' : model.revision ? '编辑日记' : '写日记';
    edit.hidden = model.editing;
    edit.disabled = !model.ready || model.loading || model.saving;
    editor.hidden = !model.editing;
    text.readOnly = model.saving;
    save.disabled = model.saving || model.loading || model.blocked || !model.ready || !model.draft?.trim();
    refresh.disabled = model.loading || model.saving;
    byId('Close').disabled = model.saving;
    status.textContent = model.message;
    status.toggleAttribute('data-error', model.error);
    body.hidden = !model.revision || (model.editing && !model.blocked && !model.error);
    if (body.textContent !== model.body) body.textContent = model.body;
    byId('Empty').hidden = !model.ready || model.loading || model.editing || model.revision !== 0 || !!model.photos.length;
    drawPhotos(model);
  }

  const photoObserver = new IntersectionObserver(changes => {
    for (const {target, isIntersecting} of changes) {
      if (!isIntersecting) continue;
      photoObserver.unobserve(target);
      void loadPhoto(target);
    }
  }, {root: page});

  async function loadPhoto(button) {
    try {
      const r = await rpc('diary_image', button.dataset.day, button.dataset.photo);
      if (!r?.ok || !/^data:image\/(?:png|jpeg|gif|webp);base64,/.test(r.image)) throw new Error();
      const img = document.createElement('img');
      img.alt = '';
      img.src = r.image;
      await img.decode();
      if (!button.isConnected) return;
      button.replaceChildren(img);
      button.disabled = false;
      button.addEventListener('click', () => window.看大图?.(img.src, button.dataset.photo));
    } catch {
      button.querySelector('span').textContent = '图片暂时打不开';
    }
  }

  function drawPhotos(model) {
    photos.hidden = !model.photos.length;
    const key = JSON.stringify([current, model.photos]);
    if (key === photoKey) return;
    photoKey = key;
    photoObserver.disconnect();
    photos.replaceChildren(...model.photos.map(photo => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'journal-photo';
      button.disabled = true;
      button.dataset.day = current;
      button.dataset.photo = photo.name;
      button.title = photo.name;
      button.setAttribute('aria-label', '查看日记配图：' + photo.name);
      const label = document.createElement('span');
      label.textContent = '正在读取…';
      button.append(label);
      photoObserver.observe(button);
      return button;
    }));
  }

  function drawDates() {
    const top = dates.scrollTop, left = dates.scrollLeft;
    const focus = dates.contains(document.activeElement) ? document.activeElement.dataset.day : undefined;
    const days = new Set([...savedDates, ...[...entries].filter(([, m]) => m.draft !== null).map(([day]) => day)]);
    dates.replaceChildren(...[...days].sort().reverse().map(day => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = day;
      button.dataset.day = day;
      button.className = 'dy-tab';
      button.classList.toggle('act', day === current);
      if (day === current) button.setAttribute('aria-current', 'date');
      button.addEventListener('click', () => select(day));
      if (day === focus) requestAnimationFrame(() => button.focus({preventScroll:true}));
      return button;
    }));
    dates.scrollTop = top;
    dates.scrollLeft = left;
  }

  async function list() {
    const token = ++listing;
    try {
      const result = await rpc('diary_dates');
      if (!result?.ok || !Array.isArray(result.dates)) throw new Error();
      if (token !== listing) return;
      if (result.dates.join('\n') !== savedDates.join('\n')) {
        savedDates = result.dates;
        drawDates();
      }
    } catch {
      if (token !== listing) return;
      const model = entry(current);
      if (!model.message) { note(model, '日记列表暂时读不到。', true); draw(); }
    }
  }

  async function read(day, background = false) {
    const model = entry(day);
    if (model.loading || model.saving) return;
    model.loading = true;
    if (!background) {
      note(model, '正在读取日记…');
      if (day === current) draw();
    }
    try {
      const result = await rpc('diary_read', day);
      if (!result?.ok || result.day !== day || !(result.revision === 0 ||
          /^[a-f0-9]{64}$/.test(result.revision))) throw new Error();
      if (model.saving) return;
      const changed = model.ready && model.revision !== result.revision && model.draft !== null;
      model.body = String(result.body);
      model.photos = Array.isArray(result.photos) ? result.photos : [];
      if (!background || !changed) {
        model.revision = result.revision;
        model.blocked = false;
      } else {
        model.blocked = true;
      }
      model.ready = true;
      if (changed) note(model, background ? '正文已在别处更新。重新读取后可检查你的草稿。'
        : '已读取最新正文，你原来的草稿仍保留。', true);
      else if (!background) note(model, '');
    } catch {
      if (!background || !model.ready) note(model, '这一天的日记暂时读不到。', true);
    } finally {
      model.loading = false;
      if (day === current) draw();
    }
  }

  function select(day) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || day === current) return;
    if (current) {
      const previous = entry(current);
      previous.top = page.scrollTop;
      previous.writePos = text.scrollTop;
    }
    current = day;
    date.value = day;
    const model = entry(day);
    text.value = model.draft ?? model.body;
    drawDates();
    draw();
    page.scrollTop = model.top;
    text.scrollTop = model.writePos;
    if (!model.ready) void read(day);
  }

  date.addEventListener('change', () => {
    if (date.value && date.validity.valid) select(date.value);
    else date.value = current;
  });
  refresh.addEventListener('click', () => { void read(current); void list(); });
  const picker = byId('PhotoFile'), addPhoto = byId('AddPhoto');
  let photoDay;
  addPhoto.addEventListener('click', () => { photoDay = current; picker.click(); });
  picker.addEventListener('change', async () => {
    const day = photoDay || current, model = entry(day), file = picker.files[0];
    picker.value = '';
    if (!file || addPhoto.disabled) return;
    addPhoto.disabled = true;
    try {
      if (file.size > 10 * 1024 * 1024) throw new Error('每张照片最多 10 MiB');
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(new Error('无法读取这张照片'));
        reader.readAsDataURL(file);
      });
      const r = await rpc('diary_add_photo', day, data);
      if (!r?.ok) throw new Error(r?.error || '配图未能保存');
      await read(day, true);
      await list();
      if (!model.blocked) note(model, r.duplicate ? '这张照片已经在日记里了' : '照片已存入当天的日记');
    } catch (error) {
      note(model, error.message || '配图未能保存', true);
    } finally {
      addPhoto.disabled = false;
      if (current === day) draw();
    }
  });
  edit.addEventListener('click', () => {
    const model = entry(current);
    model.editing = true;
    text.value = model.draft ?? model.body;
    draw();
    text.focus();
  });
  text.addEventListener('input', () => { entry(current).draft = text.value; draw(); });
  byId('Close').addEventListener('click', () => {
    entry(current).editing = false;
    draw();
    edit.focus();
  });
  save.addEventListener('click', async () => {
    const day = current, model = entry(day);
    if (save.disabled) return;
    model.saving = true;
    note(model, '正在保存…');
    draw();
    try {
      const result = await rpc('diary_save', day, model.draft, model.revision);
      if (result?.ok && result.day === day && /^[a-f0-9]{64}$/.test(result.revision)) {
        model.body = model.draft;
        model.revision = result.revision;
        model.draft = null;
        model.editing = false;
        note(model, '已保存');
        savedDates = [...new Set([...savedDates, day])];
      } else {
        model.blocked = result?.conflict === true;
        note(model, result?.error || '日记未能保存，草稿仍保留。', true);
      }
    } catch {
      note(model, '日记保存结果未确认，草稿仍保留。', true);
    } finally {
      model.saving = false;
      drawDates();
      if (day === current) draw();
    }
  });

  window.localDiary = {open() {
    if (started) { void read(current, true); void list(); return; }
    started = true;
    const today = new Date();
    select([String(today.getFullYear()).padStart(4, '0'),
      String(today.getMonth() + 1).padStart(2, '0'), String(today.getDate()).padStart(2, '0')].join('-'));
    void list();
  }};
  setInterval(() => {
    if (!document.hidden && document.querySelector('.chat.on-diary') && current) {
      void read(current, true);
      void list();
    }
  }, 6000);
})();
