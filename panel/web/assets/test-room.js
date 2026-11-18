(async () => {
  const rpc = (method, ...args) => window.pywebview.api.test_window(method, args);
  if (!window.pywebview?.api) await new Promise(resolve => window.addEventListener('pywebviewready', resolve, {once:true}));
  if (!(await rpc('configured'))?.enabled) return;
  const root = document.documentElement;
  const live = $('.appbody > .chat');
  const liveRow = $('#convList [data-chat="yu"]');
  const flask = 'data:image/svg+xml,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect width="40" height="40" rx="8" fill="#eaf1fb"/>'
    + '<path d="M16 10h8m-6 0v9l-6 10a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2l-6-10v-9m-7 15h10" '
    + 'fill="none" stroke="#446787" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>');
  const row = el('button', 'conv trial-test-row');
  row.type = 'button';
  row.dataset.chat = 'test';
  row.dataset.name = '测试窗口 本机';
  row.setAttribute('aria-pressed', 'false');
  row.innerHTML = `<span class="av"><img src="${flask}" alt=""></span>
    <span class="cbody"><span class="top"><span class="nm">测试窗口</span><span class="tm">本机</span></span>
    <span class="pv">隔离会话</span></span>`;
  liveRow.after(row);
  $('#convCount').textContent = $$('#convList .conv').length + ' 个';

  const pane = el('section', 'chat trial-test-chat');
  pane.hidden = true;
  pane.setAttribute('aria-labelledby', 'trialTestName');
  pane.innerHTML = `<header class="chat-head">
    <img class="av" src="${flask}" alt="">
    <div class="hmeta"><div class="hname"><span id="trialTestName">测试窗口</span><span class="tag" role="status"></span></div>
    <div class="hsub">本机隔离会话</div></div>
    </header>
    <div class="stream" id="trialTestStream" role="log" aria-label="测试聊天记录" tabindex="0"></div>
    <form class="composer">
      <textarea rows="2" maxlength="2000" placeholder="输入测试消息" aria-label="测试消息"></textarea>
      <div class="barline"><span class="grow trial-test-feedback" role="status"></span></div>
    </form>`;
  live.after(pane);
  const stream = pane.querySelector('.stream');
  const input = pane.querySelector('textarea');
  const status = pane.querySelector('.tag');
  const feedback = pane.querySelector('.trial-test-feedback');
  const button = $('#sendBtn').cloneNode(true);
  button.id = 'trialTestSend';
  button.type = 'submit';
  feedback.after(button);

  const active = () => root.dataset.trialChat === 'test';
  const seen = new Set();
  let historyId = null, cursor = 0, before = null, connected = null;
  let refreshing, loadingOlder = false, sending = false;

  function syncSend() {
    button.disabled = connected !== true || sending || !input.value.trim();
  }

  function drawStatus() {
    const label = connected === null ? '连接中' : (connected ? '在线' : '未接线');
    status.textContent = label;
    if (active()) {
      顶栏状态({phase: connected ? 'connected' : (connected === null ? 'connecting' : 'offline')});
      $('#tbState').textContent = '测试' + label;
    }
    syncSend();
  }

  function appendRows(rows, older = false) {
    const previousHeight = stream.scrollHeight;
    const previousTop = stream.scrollTop;
    const follow = previousHeight - previousTop - stream.clientHeight < 75;
    const fragment = document.createDocumentFragment();
    let latest;
    for (const row of rows) {
      if (seen.has(row.message_id)) continue;
      seen.add(row.message_id);
      const message = 转换(row);
      if (!message) continue;
      const bubble = msgNode(message, 'test');
      bubble.querySelector('img.mav').src = message.k === 'me' ? ME_AV : flask;
      fragment.append(bubble);
      latest = message;
    }
    if (!latest) return;
    stream.querySelector('.sys')?.remove();
    if (older) {
      stream.prepend(fragment);
      stream.scrollTop = stream.scrollHeight - previousHeight + previousTop;
    } else {
      stream.append(fragment);
      if (follow || latest.k === 'me') stream.scrollTop = stream.scrollHeight;
    }
  }

  async function loadLatest() {
    const result = await rpc('history', 30, 0);
    if (!result?.ok) throw new Error();
    historyId = result.history_id;
    cursor = result.live_cursor;
    before = result.还有更早 ? result.前游标 : null;
    seen.clear();
    stream.replaceChildren(el('div', 'sys', '还没有测试记录'));
    appendRows(result.消息);
    stream.scrollTop = stream.scrollHeight;
  }

  function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const result = await rpc('poll', cursor, historyId);
      if (!result?.ok) throw new Error();
      if (result.reset) await loadLatest();
      else {
        appendRows(result.来信);
        cursor = result.current;
      }
      connected = result.connected === true;
    })().catch(() => {
      connected = false;
      if (!historyId) stream.replaceChildren(el('div', 'sys', '测试记录暂时读不到'));
    }).finally(() => {
      refreshing = null;
      drawStatus();
    });
    return refreshing;
  }

  async function loadOlder() {
    if (!active() || loadingOlder || before === null || stream.scrollTop >= 100) return;
    const key = historyId;
    loadingOlder = true;
    try {
      const result = await rpc('history', 30, 0, before);
      if (!result?.ok || result.history_id !== key || historyId !== key) return;
      before = result.还有更早 ? result.前游标 : null;
      appendRows(result.消息, true);
    } catch {
      // Keep the cursor so scrolling can retry a failed page.
    } finally { loadingOlder = false; }
  }

  async function submit(event) {
    event.preventDefault();
    const draft = input.value;
    if (button.disabled || !draft.trim() || !active()) return;
    sending = true;
    feedback.textContent = '';
    syncSend();
    try {
      const receipt = await rpc('send', draft);
      if (!receipt?.ok) {
        feedback.textContent = receipt?.error || '测试提交未确认';
        if (receipt?.code === 'route_unavailable') {
          connected = false;
          drawStatus();
        }
      } else if (draft === input.value) input.value = '';
    } catch {
      feedback.textContent = '测试提交未确认，请先核对记录';
    } finally {
      // Render test messages only from the persisted feed, even if a poll races the receipt.
      if (refreshing) await refreshing;
      await refresh();
      sending = false;
      syncSend();
    }
  }

  const selectLive = pickChat, selectFeature = 切功能;
  function leave(callback) {
    if (!active()) return callback();
    delete root.dataset.trialChat;
    live.hidden = live.inert = false;
    pane.hidden = true;
    row.setAttribute('aria-pressed', 'false');
    const result = callback();
    window.dispatchEvent(new Event('trial-live-visible'));
    return result;
  }

  function open() {
    if ($('#pondAsk').classList.contains('on')) return;
    if (!active()) {
      if (cur !== 'yu') selectLive('yu');
      root.dataset.trialChat = 'test';
      cur = 'test';
      切页('chat');
      live.hidden = live.inert = true;
      pane.hidden = false;
      $$('.conv').forEach(item => item.classList.toggle('act', item === row));
      row.setAttribute('aria-pressed', 'true');
      if (!stream.hasChildNodes()) stream.append(el('div', 'sys', '正在读取测试记录…'));
      drawStatus();
      void refresh();
    }
    closeDrawer();
    input.focus({preventScroll: true});
  }

  pickChat = id => id === 'test' ? open() : leave(() => selectLive(id));
  切功能 = id => leave(() => selectFeature(id));
  row.addEventListener('click', open);
  $('[data-nav="chat"]').addEventListener('click', event => {
    if (!active()) return;
    event.stopImmediatePropagation();
    if (window.matchMedia('(max-width:940px)').matches) openDrawer();
    else input.focus();
  }, true);
  pane.querySelector('form').addEventListener('submit', submit);
  input.addEventListener('input', syncSend);
  input.addEventListener('keydown', event => {
    if (event.isComposing || event.keyCode === 229 || event.key !== 'Enter' || event.shiftKey) return;
    void submit(event);
  });
  stream.addEventListener('scroll', loadOlder, {passive: true});
  setInterval(() => { if (active()) void refresh(); }, 1200);
  syncSend();
  if (new URLSearchParams(location.search).get('chat') === 'test') open();
})().catch(() => {});
