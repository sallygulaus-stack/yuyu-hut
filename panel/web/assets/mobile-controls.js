(() => {
  'use strict';
  function mount(root, api, {existingPairing = false} = {}) {
    if (!root) return;
    root.innerHTML = `
      <h4>手机聊天</h4>
      <p class="sc-caption" data-mobile-state>正在读取…</p>
      <label data-mobile-port-label>${existingPairing ? '本机端口（留空用原端口）' : '本机端口（可留空）'}
        <input type="number" min="1024" max="65535" step="1"
        inputmode="numeric" name="mobile-port"></label>
      <div class="sc-actions">
        <button class="sc-button sc-primary" type="button" data-mobile-toggle>开启手机入口</button>
        <button class="sc-button" type="button" data-mobile-renew hidden>重新配对</button>
        <button class="sc-button" type="button" data-mobile-copy hidden>复制配对码</button>
      </div>
      <output data-mobile-url hidden></output>
      <p class="sc-caption" data-mobile-port-note hidden></p>
      <input type="text" data-mobile-code aria-label="手机配对码" autocomplete="off" spellcheck="false" readonly hidden>
      <p class="sc-feedback" role="status"></p>`;
    const get = selector => root.querySelector(selector), state = get('[data-mobile-state]');
    const portLabel = get('[data-mobile-port-label]'), port = portLabel.querySelector('input');
    const toggle = get('[data-mobile-toggle]'), renew = get('[data-mobile-renew]');
    const url = get('[data-mobile-url]'), code = get('[data-mobile-code]');
    const portNote = get('[data-mobile-port-note]');
    const copy = get('[data-mobile-copy]'), feedback = get('.sc-feedback');
    let current, busy = false, rounds = 0;
    function draw() {
      const enabled = current?.enabled === true;
      toggle.textContent = enabled ? '关闭手机入口' : '开启手机入口';
      toggle.disabled = busy || !current;
      renew.hidden = !enabled || existingPairing;
      url.hidden = !enabled;
      renew.disabled = busy;
      portLabel.hidden = enabled;
      if (existingPairing) port.placeholder = current?.default_port || '';
      state.textContent = !current ? '手机入口状态暂时读不到' : !enabled ? '未开启' :
        existingPairing ? '已开启 · 沿用原手机授权' : current.paired ? '已配对' :
        current.code ? '等待手机配对' : '配对码已到期';
      url.textContent = enabled ? current.url : '';
      portNote.hidden = !existingPairing || !enabled || current.port === current.default_port;
      portNote.textContent = portNote.hidden ? '' : `原手机转发仍指向 ${current.default_port}。留空重开可恢复。`;
      code.value = enabled && !existingPairing ? current.code || '' : '';
      code.hidden = copy.hidden = !code.value;
    }
    async function refresh() {
      const round = ++rounds;
      try {
        const data = await api.mobile_view();
        if (round === rounds && !busy && data?.ok) { current = data; draw(); }
      } catch { if (!current && round === rounds) draw(); }
    }
    async function action(name) {
      if (busy) return;
      if (name === 'start' && !port.reportValidity()) return;
      busy = true;
      rounds++;
      feedback.textContent = '';
      draw();
      try {
        const result = await api.mobile_manage(name, name === 'start' && port.value ? Number(port.value) : null);
        if (result?.ok) current = result;
        else feedback.textContent = result?.error || '手机入口操作未完成';
      } catch { feedback.textContent = '手机入口操作未完成'; }
      finally { busy = false; draw(); }
    }
    toggle.addEventListener('click', () => void action(current.enabled ? 'stop' : 'start'));
    renew.addEventListener('click', () => void action('renew'));
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.value);
        feedback.textContent = '已复制配对码';
      } catch {
        code.focus();
        code.select();
      }
    });
    draw();
    void refresh();
    setInterval(() => { if (!document.hidden && root.getClientRects().length) void refresh(); }, 3000);
  }
  window.MobileControls = {mount};
})();
