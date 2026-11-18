/* Connection and service status rendering for the QQ-style shell. */
'use strict';

function 顶栏状态(r) {
  const d = $('#tbDot'), s = $('#tbState');
  if (!d || !s) return;
  d.className = 'tb-dot';
  if (r?.endpoint_id === 'demo') {
    d.classList.add('off'); s.textContent = '离线演示';
    return;
  }
  const phase = String(r && r.phase || 'offline');
  if (phase === 'configured' || phase === 'connecting' || phase === 'degraded') {
    d.classList.add('warn'); s.textContent = phase === 'degraded' ? '降级' : '已配置';
  } else if (phase === 'connected') { d.classList.add('on'); s.textContent = '在线'; }
  else if (phase === 'endpoint_changed') { d.classList.add('warn'); s.textContent = '需重新绑定'; }
  else if (phase === 'disabled') { d.classList.add('off'); s.textContent = '未启用'; }
  else { d.classList.add('off'); s.textContent = String((r && r.error) || '离线').slice(0, 12); }
}

async function 刷服务() {
  const p = window.pywebview && window.pywebview.api;
  if (!p) return;
  const 控制台 = document.querySelector('[data-service-console]');
  if (控制台 && window.ServiceConsole) return window.ServiceConsole.mount(控制台, p);
  try {
    const r = await p.state();
    const box = $('#svcList');
    if (box) {
      box.replaceChildren();
      (r.services || []).forEach(sv => {
        const row = el('div', 'srow');
        const dot = sv.phase === 'degraded' ? 'warn' : (sv.up ? 'on' : (sv.phase === 'error' ? 'off' : (sv.configured ? 'warn' : 'off')));
        row.append(el('span', 'dot ' + dot), el('b', '', sv.label), el('span', '', sv.detail || ''));
        box.append(row);
      });
    }
  } catch (e) {}
  setTimeout(刷服务, 6000);
}

async function 探状态(只刷一次 = false) {
  try {
    const p = window.pywebview && window.pywebview.api;
    if (p) {
      const r = await p.chat_ready();
      if (cur === 'yu') {
        $('#chatSub').textContent = r.label || r.error || '离线';
        $('.chat-head .hname .tag').textContent = r.endpoint_id === 'demo' ? '演示' :
          r.phase === 'endpoint_changed' ? '需重新绑定' : r.phase === 'degraded' ? '降级' :
          (r.connected || r.通 ? '在线' : (r.phase === 'configured' ? '已配置' : '离线'));
      }
      if (document.documentElement.dataset.trialChat !== 'test') 顶栏状态(r);
    }
  } catch (e) {}
  if (!只刷一次) setTimeout(探状态, 6000);
}

window.addEventListener('panel-connection-changed', () => {
  探状态(true);
  if (typeof 拉资料 === 'function') 拉资料();
});
window.顶栏状态 = 顶栏状态;
window.刷服务 = 刷服务;
window.探状态 = 探状态;
