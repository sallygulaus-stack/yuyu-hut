/* Personal appearance cards. Connection diagnostics live in the console. */
(() => {
  'use strict';

  const mask = $('#profileMask');
  if (!mask || typeof window.createProfileAppearance !== 'function') return;
  const body = $('#profileBody');
  const retry = $('#profileRetry');
  const note = $('#profileNote');
  const name = $('#profileName');
  const avatar = $('#profileAvatar');
  const presence = $('#profilePresence');
  const tabs = [$('#profileMeTab'), $('#profilePeerTab')];
  let role = 'peer', revision = 0, opener, ready = false;
  const editor = window.createProfileAppearance({
    anchor: note,
    onEdit(active) {
      note.hidden = !ready || active;
    },
    async onSaved(next, message) {
      if (await open(next)) {
        editor.say(message);
        editor.focusEdit();
      }
    },
  });

  function draw(payload) {
    const profile = payload.profile;
    avatar.src = profile.avatar || ('assets/default-' + role + '.png');
    name.textContent = profile.display_name || (role === 'me' ? '我' : '聊天助手');
    $('#profileSignature').textContent = profile.signature || '还没有留下签名。';
    const status = payload.status || {};
    const online = status.connected === true;
    $('#profileDot').classList.toggle('on', online);
    $('#profileDot').classList.toggle('off', !online);
    $('#profileState').textContent = online ? '在线' : (status.phase === 'disabled' ? '暂未接通' : '暂不在线');
    presence.hidden = role === 'me';
    ready = true;
    editor.show(role, payload);
  }

  async function open(next = 'peer', source = document.activeElement) {
    if (!mask.classList.contains('on')) opener = source;
    const current = ++revision;
    role = next;
    mask.classList.add('on');
    const activeTab = tabs[next === 'me' ? 0 : 1];
    for (const tab of tabs) {
      const active = tab === activeTab;
      tab.classList.toggle('act', active);
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
    }
    body.setAttribute('aria-labelledby', activeTab.id);
    body.scrollTop = 0;
    $('#profileTitle').textContent = activeTab.textContent;
    avatar.removeAttribute('src');
    name.textContent = '正在打开名片…';
    presence.hidden = true;
    ready = false;
    retry.hidden = true;
    editor.hide();
    activeTab.focus({preventScroll: true});
    try {
      const api = window.pywebview?.api;
      const result = await api[next === 'me' ? 'my_profile' : 'peer_profile']();
      if (!result?.ok || !result.profile) throw new Error();
      if (current !== revision) return false;
      draw(result);
      return true;
    } catch {
      if (current === revision) {
        name.textContent = '名片暂时打开不了';
        retry.hidden = false;
      }
      return false;
    }
  }
  window.打开资料 = open;

  function close() {
    revision++;
    ready = false;
    editor.hide();
    mask.classList.remove('on');
    opener?.focus({preventScroll: true});
    opener = null;
  }
  $('#profileClose').addEventListener('click', close);
  mask.addEventListener('click', e => { if (e.target === mask) close(); });
  tabs[0].addEventListener('click', () => open('me'));
  tabs[1].addEventListener('click', () => open('peer'));
  retry.addEventListener('click', () => open(role));
  document.addEventListener('keydown', e => {
    if (!mask.classList.contains('on') || e.isComposing) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      if (!editor.cancelEdit()) close();
    } else if (tabs.includes(e.target) && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      open(e.key === 'Home' ? 'me' : e.key === 'End' ? 'peer' : role === 'me' ? 'peer' : 'me');
    } else if (e.key === 'Tab') {
      const focusable = [...mask.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled)')]
        .filter(node => node.tabIndex >= 0 && node.getClientRects().length);
      const index = focusable.indexOf(document.activeElement);
      if (index < 0 || (e.shiftKey ? index === 0 : index === focusable.length - 1)) {
        e.preventDefault();
        (e.shiftKey ? focusable.at(-1) : focusable[0])?.focus();
      }
    }
  }, true);
})();
