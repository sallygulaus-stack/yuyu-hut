(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const pair = byId('pair'), code = byId('pair-code'), chat = byId('chat'), stream = byId('stream');
  const messages = byId('messages'), more = byId('older'), text = byId('text'), file = byId('file');
  const send = byId('send'), add = byId('add'), remove = byId('remove');
  const viewer = byId('viewer'), fullImage = byId('full-image');
  const tokenKey = 'fish-house-mobile-session';
  const labels = {
    unknown: '投递结果未知', saved: '仅保存在本机', failed: '发送失败',
    framework_unknown: '处理结果未知', framework_waiting: '等待处理',
    gateway_accepted: '网关已接收', framework_accepted: '服务已接收',
  };
  const placeholders = {image: '图片暂不可读', record: '语音暂不可读', face: '表情', file: '文件', video: '视频'};
  const media = /^\/media\/(?:image|record)\/[^/]+\/[0-9a-f]{64}$/;
  const avatars = /^\/(?:(?:me|peer)\.png|avatar\/(?:me|peer)\/[0-9a-f]{24}\.png\/[0-9a-f]{64})$/;
  const defaults = {me: {name: '我', avatar: '/me.png'}, peer: {name: '聊天助手', avatar: '/peer.png'}};
  let token = '';
  try { token = sessionStorage.getItem(tokenKey) || ''; } catch {}
  let people = defaults, limits = {}, draft, draftUrl = '', first = 0, next = 0;
  let loading = false, sending = false, paired = false, epoch = 0, nextProfile = 0;
  let followsLatest = true;

  function keepLatestVisible() {
    if (paired && followsLatest) stream.scrollTop = stream.scrollHeight;
  }

  stream.addEventListener('scroll', () => {
    followsLatest = stream.scrollHeight - stream.clientHeight - stream.scrollTop < 75;
  }, {passive: true});
  // Images can resize the history and a draft or keyboard can resize its viewport.
  const historySize = new ResizeObserver(keepLatestVisible);
  historySize.observe(messages);
  historySize.observe(stream);

  function node(tag, className, value) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (value) result.textContent = value;
    return result;
  }

  function store(value) {
    token = value;
    try {
      if (value) sessionStorage.setItem(tokenKey, value);
      else sessionStorage.removeItem(tokenKey);
    } catch {}
  }

  function controls() {
    const online = paired && limits.connected;
    add.disabled = !online || !limits.can_image || sending;
    remove.disabled = sending;
    text.readOnly = sending;
    const hasDraft = draft ? limits.can_image : limits.can_text && text.value.trim();
    send.disabled = !online || sending || !hasDraft;
    more.hidden = first === 0;
    more.disabled = loading;
  }

  function profile() {
    const name = paired ? people.peer.name : '鱼鱼小窝';
    byId('peer-name').textContent = name;
    byId('peer-avatar').src = paired ? people.peer.avatar : '/peer.png';
    byId('connection').textContent = !paired ? '手机聊天' : limits.connected ? '已连接' : '聊天端暂未连接';
    for (const image of messages.querySelectorAll('[data-avatar]')) {
      const source = people[image.dataset.avatar].avatar;
      if (image.getAttribute('src') !== source) image.src = source;
    }
    for (const name of messages.querySelectorAll('[data-name]')) name.textContent = people[name.dataset.name].name;
    controls();
  }

  function locked(message = '') {
    epoch++;
    store('');
    paired = false;
    limits = {};
    first = next = 0;
    if (viewer.open) viewer.close();
    messages.replaceChildren();
    pair.hidden = false;
    chat.hidden = byId('leave').hidden = true;
    byId('pair-error').textContent = message;
    profile();
  }

  async function rpc(name, payload = {}) {
    const was = token;
    const headers = {'Content-Type': 'application/json'};
    if (name !== 'pair') headers.Authorization = 'Bearer ' + was;
    const response = await fetch('/api/' + name, {method: 'POST', headers, body: JSON.stringify(payload)});
    if (response.status === 401 && name !== 'pair' && token === was) locked('请重新配对。');
    return response.json();
  }

  async function refreshProfile() {
    const request = epoch;
    const result = await rpc('session');
    if (request !== epoch || !result.ok) return false;
    limits = result;
    people = Object.fromEntries(Object.entries(defaults).map(([role, fallback]) => {
      const value = result.people?.[role];
      return [role, {name: value?.name || fallback.name,
        avatar: avatars.test(value?.avatar || '') ? value.avatar : fallback.avatar}];
    }));
    nextProfile = Date.now() + 9000;
    return true;
  }

  function row(message) {
    const role = message.mine ? 'me' : 'peer';
    const article = node('article', 'msg' + (message.mine ? ' mine' : ''));
    article.dataset.sequence = String(message.sequence);
    const avatar = node('img', 'avatar');
    avatar.alt = '';
    avatar.dataset.avatar = role;
    avatar.src = people[role].avatar;
    const contents = node('div', 'content');
    const name = node('span', 'sender', people[role].name);
    name.dataset.name = role;
    contents.append(name);
    for (const part of message.parts) {
      if (part.type === 'text') contents.append(node('p', 'bubble', part.text));
      else if (part.type === 'image' && media.test(part.url)) {
        const button = node('button', 'photo');
        button.type = 'button';
        button.setAttribute('aria-label', '查看聊天图片');
        const image = node('img');
        image.alt = '聊天图片';
        image.loading = 'lazy';
        image.src = part.url;
        image.addEventListener('error', () => {
          button.replaceWith(node('p', 'bubble', placeholders.image));
        });
        button.addEventListener('click', () => {
          fullImage.src = part.url;
          viewer.showModal();
        });
        button.append(image);
        contents.append(button);
      } else if (part.type === 'record' && media.test(part.url)) {
        const audio = node('audio');
        audio.preload = 'none';
        audio.controls = true;
        audio.src = part.url;
        audio.addEventListener('error', () => audio.replaceWith(node('p', 'bubble', placeholders.record)));
        contents.append(audio);
      } else if (placeholders[part.type]) contents.append(node('p', 'bubble', placeholders[part.type]));
    }
    if (message.mine && labels[message.delivery]) contents.append(node('span', 'sender', labels[message.delivery]));
    article.append(avatar, contents);
    const fragment = document.createDocumentFragment();
    fragment.append(article);
    if (message.split) fragment.append(node('p', 'split', '这里开了新的聊天分段'));
    return fragment;
  }

  async function history(direction = 'latest') {
    if (loading || !paired) return;
    loading = true;
    const request = epoch;
    controls();
    try {
      const payload = direction === 'after' ? {after: next} : direction === 'before' ? {before: first} : {};
      let result = await rpc('history', payload);
      if (request !== epoch || !result.ok) return;
      if (result.reset) {
        direction = 'latest';
        result = await rpc('history');
        if (request !== epoch || !result.ok) return;
      }
      const items = document.createDocumentFragment();
      for (const item of result.messages) items.append(row(item));
      byId('empty').hidden = result.total > 0;
      if (direction === 'latest') {
        first = result.first;
        next = result.next;
        more.hidden = first === 0;
        messages.replaceChildren(items);
        followsLatest = true;
        keepLatestVisible();
      } else if (direction === 'before') {
        const anchor = messages.firstElementChild;
        const top = anchor?.getBoundingClientRect().top;
        first = result.first;
        more.hidden = first === 0;
        messages.prepend(items);
        // Account for the browser already adjusting some or all of the displacement.
        if (anchor) {
          const displacement = anchor.getBoundingClientRect().top - top;
          stream.scrollTop += displacement;
        }
      } else {
        next = result.next;
        messages.append(items);
        if (result.messages.length) keepLatestVisible();
      }
    } catch {
      if (paired && request === epoch) byId('connection').textContent = '暂时无法读取聊天';
    } finally {
      loading = false;
      controls();
    }
  }

  async function open() {
    const request = ++epoch;
    try {
      if (!await refreshProfile() || request !== epoch) return;
      paired = true;
      pair.hidden = true;
      chat.hidden = byId('leave').hidden = false;
      profile();
      await history();
    } catch {
      if (request === epoch) locked('暂时连不到小窝，请重新配对。');
    }
  }

  function setDraft(value) {
    if (draftUrl) URL.revokeObjectURL(draftUrl);
    draft = value;
    draftUrl = value ? URL.createObjectURL(value) : '';
    const preview = byId('draft-image');
    if (draftUrl) preview.src = draftUrl;
    else preview.removeAttribute('src');
    byId('draft-name').textContent = value?.name || '';
    byId('draft').hidden = !value;
    controls();
  }

  function encode(image) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1]);
      reader.onerror = reject;
      reader.readAsDataURL(image);
    });
  }

  pair.addEventListener('submit', async event => {
    event.preventDefault();
    const button = byId('pair-submit');
    button.disabled = true;
    const error = byId('pair-error');
    error.textContent = '';
    try {
      const result = await rpc('pair', {code: code.value.trim()});
      if (!result.ok) { error.textContent = result.error; return; }
      code.value = '';
      store(result.token);
      await open();
    } catch {
      error.textContent = '暂时连不到小窝。';
    } finally {
      button.disabled = false;
    }
  });
  byId('leave').addEventListener('click', async () => {
    const leave = byId('leave');
    leave.disabled = true;
    try {
      const result = await rpc('leave');
      if (result.ok) {
        text.value = '';
        setDraft(null);
        locked();
      }
    } catch {
      byId('connection').textContent = '断开结果未确认';
    } finally { leave.disabled = false; }
  });
  more.addEventListener('click', () => void history('before'));
  add.addEventListener('click', () => file.click());
  remove.addEventListener('click', () => setDraft(null));
  text.addEventListener('input', controls);
  file.addEventListener('change', () => {
    const image = file.files[0];
    file.value = '';
    if (!image) return;
    const status = byId('send-status');
    const extension = /\.(?:png|jpe?g|gif|webp|bmp|apng)$/i;
    if (!image.size || image.size > limits.image_limit || !extension.test(image.name)) {
      status.textContent = '这张图的格式或大小不符合当前连接要求。';
      return;
    }
    status.textContent = '';
    setDraft(image);
  });
  byId('compose').addEventListener('submit', async event => {
    event.preventDefault();
    if (send.disabled) return;
    const request = epoch;
    const status = byId('send-status');
    const caption = text.value;
    sending = true;
    status.textContent = '正在发送…';
    controls();
    try {
      const result = draft ? await rpc('image', {image: await encode(draft), name: draft.name, text: caption})
        : await rpc('send', {text: caption});
      if (request !== epoch) return;
      if (result.ok || result.recorded) {
        text.value = '';
        setDraft(null);
        void history('after');
      }
      status.textContent = result.ok ? '' : result.error || '发送结果未确认，请先查看聊天。';
    } catch {
      status.textContent = '发送结果未确认，请先查看聊天。';
    } finally {
      sending = false;
      controls();
    }
  });
  byId('close-image').addEventListener('click', () => viewer.close());
  viewer.addEventListener('close', () => fullImage.removeAttribute('src'));

  async function tick() {
    if (paired && !document.hidden) {
      if (Date.now() > nextProfile) {
        try { if (await refreshProfile()) profile(); }
        catch { byId('connection').textContent = '暂时连不到小窝'; }
      }
      await history('after');
    }
    setTimeout(tick, 1800);
  }
  if (token) void open();
  setTimeout(tick, 1800);
})();
