/* Local console views. The copied diagnostic is limited to diagnosticRows. */
(() => {
  'use strict';
  const phases = {
    configured: '已配置', connected: '已连接', connecting: '正在连接', degraded: '部分可用',
    disabled: '未启用', disconnected: '未连接', endpoint_changed: '身份已变化，需重新绑定', error: '连接异常',
    offline: '未连接',
  };
  const modes = {
    demo: '离线演示', dsh_local: 'DSH 本地网关', dsh_remote: 'DSH HTTPS 网关',
    onebot: 'OneBot 本地端', model: '模型适配器', trial: '现有私聊', preview: '只读预览',
    test_window: '本机测试桥',
  };
  const caps = {
    text: '文字', image: '图片', record: '语音文件', idempotent_send: '幂等发送',
    delivery_receipts: '处理回执', history_context: '会话上下文',
  };
  const detailCaps = {
    ...caps, media_ids: '媒体 ID', media_upload: '上传媒体', media_download: '下载媒体',
    history_paging: '历史分页', profiles: '资料', event_resume: '断点续收',
    deduplication: '事件去重', http_poll: 'HTTP 轮询', message_status: '投递状态',
  };
  const mounted = new WeakMap();
  const node = (tag, text, cls) => {
    const result = document.createElement(tag);
    if (text) result.textContent = text;
    if (cls) result.className = cls;
    return result;
  };

  async function copyText(value, feedback) {
    try {
      await navigator.clipboard.writeText(value);
      feedback.textContent = '已复制';
      return;
    } catch {}
    const fallback = node('textarea', '', 'sc-copy-fallback');
    fallback.readOnly = true;
    fallback.value = value;
    feedback.replaceChildren(fallback);
    fallback.focus();
    fallback.select();
    try {
      if (document.execCommand('copy')) feedback.textContent = '已复制';
    } catch {}
  }

  function dataRow(path, open, label = '数据位置') {
    const row = node('div', '', 'sc-data');
    const value = node('code', path);
    value.tabIndex = 0;
    value.setAttribute('aria-label', '数据位置');
    const tools = node('div', '', 'sc-actions');
    const feedback = node('p', '', 'sc-feedback');
    feedback.setAttribute('role', 'status');
    const copy = node('button', '复制位置', 'sc-button');
    copy.type = 'button';
    copy.addEventListener('click', () => copyText(path, feedback));
    if (open) {
      const button = node('button', '打开目录', 'sc-button');
      button.type = 'button';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const result = await open();
          feedback.textContent = result.ok ? '已打开' : (result.error || '未能打开目录');
        } catch {
          feedback.textContent = '未能打开目录';
        } finally {
          button.disabled = false;
        }
      });
      tools.append(button);
    }
    tools.append(copy);
    row.append(node('b', label), value, tools, feedback);
    return row;
  }

  function fromProfile(profile, mode) {
    return {mode, phase: profile?.status?.phase,
      capabilities: Object.entries(caps)
        .filter(([key]) => profile?.capabilities?.[key] === true).map(([, value]) => value)};
  }

  function modeText(mode) {
    return modes[mode] || '启动配置';
  }

  function phaseText(value) {
    if (value?.mode === 'demo') return '离线演示';
    if (value?.framework === false) return '网关可达，等待机器人框架';
    return phases[value?.phase] || '未知';
  }

  function diagnosticRows(data) {
    return [
      ['连接类型', modeText(data?.mode)],
      ['连接状态', phaseText(data)],
      ['消息能力', (data?.capabilities || []).filter(value => Object.values(caps).includes(value)).join('、') || '未确认'],
    ];
  }

  function diagnostics(load, first, {actions = [], onShow = () => {}} = {}) {
    const region = node('div', '', 'sc-diagnostics');
    const rows = node('dl');
    const tools = node('div', '', 'sc-actions');
    const refresh = node('button', '刷新诊断', 'sc-button');
    const copy = node('button', '复制脱敏诊断', 'sc-button');
    const message = node('p', '', 'sc-feedback');
    message.setAttribute('role', 'status');
    refresh.type = copy.type = 'button';
    refresh.dataset.chatDiagnose = '';
    let current = null;
    let revision = 0;
    function show(value) {
      current = value;
      copy.disabled = !current;
      rows.replaceChildren();
      if (current) for (const [label, text] of diagnosticRows(current)) {
        rows.append(node('dt', label), node('dd', text));
      }
      onShow(current);
    }
    async function update() {
      const request = ++revision;
      refresh.disabled = true;
      show(null);
      message.textContent = '正在读取诊断…';
      try {
        const value = await load();
        if (request !== revision) return;
        if (!value || value.ok === false) throw new Error();
        show(value);
        message.textContent = '';
      } catch {
        if (request === revision) message.textContent = '暂时读不到连接状态';
      } finally {
        if (request === revision) refresh.disabled = false;
      }
    }
    refresh.addEventListener('click', update);
    copy.addEventListener('click', () => {
      if (current) copyText(diagnosticRows(current).map(([key, val]) => `${key}：${val}`).join('\n'), message);
    });
    tools.append(refresh, copy, ...actions);
    region.append(rows, tools, message);
    show(first);
    return {region, update, show};
  }

  function detailText(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    const text = value.trim();
    if (/\/\/|\\|\b[a-z]:\/|(?:^|[\s("'=~])\/\S/i.test(text) ||
        /\b(?:\d{1,3}\.){3}\d{1,3}\b|(?:[\da-f]{0,4}:){2,}[\da-f]{0,4}|\b(?:localhost|(?:[a-z\d-]+\.)+[a-z]{2,})\b/i.test(text) ||
        /\b(?:token|secret|password|passwd|api[_ -]?key|authorization)\s*[:=]|\bbearer\s+\S/i.test(text)) {
      return '敏感明细已省略';
    }
    return text.slice(0, 500);
  }

  function connectionDetails() {
    const details = node('details', '', 'sc-connection-details');
    details.dataset.consoleConnectionDetails = '';
    const rows = node('dl');
    const feedback = node('p', '', 'sc-feedback');
    feedback.setAttribute('role', 'status');
    const join = (...values) => values.map(detailText).filter(Boolean).join(' · ');
    const sources = {local: '本地', dsh: 'DSH', onebot: 'OneBot'};
    function show(profile) {
      rows.replaceChildren();
      feedback.textContent = '';
      if (!details.open) return;
      if (!profile?.ok || !profile.status || !profile.endpoint) {
        if (profile) feedback.textContent = '暂时读不到连接诊断';
        return;
      }
      const {status, endpoint, me, peer, capabilities} = profile;
      const fields = [
        ['错误详情', detailText(status.error) || '无'],
        ['连接端点', join(endpoint.display_name, endpoint.endpoint_id)],
        ['所在位置', detailText(endpoint.location)],
        ['协议版本', detailText(endpoint.protocol_version || profile.protocol_version)],
        ['端版本', detailText(endpoint.version || status.version || peer?.version)],
        ['我的 ID', detailText(me?.identity || me?.profile_id)],
        ['我的资料来源', sources[me?.source]],
        ['我的角色', detailText(me?.role)],
        ['对方 ID', detailText(peer?.identity || peer?.profile_id)],
        ['对方资料来源', sources[peer?.source]],
        ['对方角色', detailText(peer?.role)],
        ['声音', join(peer?.voice?.name, peer?.voice?.format)],
        ['连接能力', Object.entries(detailCaps)
          .filter(([key]) => capabilities?.[key] === true).map(([, label]) => label).join('、')],
      ];
      if (status.endpoint_changed === true) fields.splice(2, 0,
        ['原网关身份', detailText(status.bound_endpoint_id)],
        ['观察到的网关身份', detailText(status.observed_endpoint_id)]);
      for (const [label, value] of fields) rows.append(node('dt', label), node('dd', value || '未确认'));
    }
    details.append(node('summary', '连接诊断'), rows, feedback);
    return {region: details, show};
  }

  const formFeedback = form => form.querySelector('.sc-feedback');
  async function run(container, task) {
    const feedback = formFeedback(container);
    const buttons = [...container.querySelectorAll('button')];
    const disabled = buttons.map(btn => btn.disabled);
    buttons.forEach(btn => { btn.disabled = true; });
    feedback.dataset.kind = '';
    feedback.textContent = '处理中…';
    try {
      const result = await task();
      feedback.dataset.kind = result.ok ? 'ok' : 'error';
      feedback.textContent = result.label || result.error || (result.ok ? '已保存' : '操作未完成');
      return result;
    } catch {
      feedback.dataset.kind = 'error';
      feedback.textContent = '面板暂时无法处理此操作';
      return null;
    } finally {
      buttons.forEach((btn, index) => { btn.disabled = disabled[index]; });
    }
  }

  function localLink(link, address) {
    let valid = false;
    try {
      const url = new URL(address);
      valid = url.protocol === 'http:' && ['127.0.0.1', '[::1]'].includes(url.hostname) &&
        !url.username && !url.password && !url.search && !url.hash && url.pathname === '/';
      if (valid) link.href = url.href;
    } catch {}
    if (!valid) link.removeAttribute('href');
    link.hidden = !valid;
  }

  function attachService(form, kind, api, entry) {
    const enabled = form.elements.enabled;
    const address = form.elements.address;
    const out = form.querySelector('[data-sample-output]');
    const preview = form.querySelector('[data-sample]');
    const entered = () => ({enabled: enabled.checked, base_url: address.value});
    let saved = entry;
    enabled.checked = entry.enabled;
    address.value = entry.base_url;
    function update() {
      const cfg = entered();
      preview.disabled = !saved.enabled || cfg.enabled !== saved.enabled ||
        cfg.base_url.replace(/\/+$/, '') !== saved.base_url;
    }
    update();
    form.addEventListener('input', update);
    form.querySelector('[data-service-test]').addEventListener('click', async () => {
      if (form.reportValidity()) await run(form, () => api.console_service(kind, entered(), 'test'));
      update();
    });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const desired = entered();
      const result = await run(form, () => api.console_service(kind, desired, 'save'));
      if (result?.ok) {
        saved = result.settings;
        address.value = saved.base_url;
        out.replaceChildren();
      }
      update();
    });
    preview.addEventListener('click', async () => {
      if (!form.reportValidity()) return;
      out.replaceChildren();
      const result = await run(form, async () => {
        const reply = await api.console_sample(kind, form.elements.sample?.value || '');
        const acceptable = kind === 'image' ? ['image/png'] : ['audio/mpeg', 'audio/wav'];
        if (reply.ok && acceptable.includes(reply.mime) && /^[A-Za-z0-9+/=]+$/.test(reply.media || '')) {
          const media = node(kind === 'image' ? 'img' : 'audio', '', 'sc-sample');
          if (kind === 'image') media.alt = 'ComfyUI 测试图';
          else media.controls = true;
          media.src = `data:${reply.mime};base64,${reply.media}`;
          out.append(media);
          if (kind === 'image' && /^[a-f0-9]{32}$/.test(reply.preview_id || '')) {
            const actions = node('div', '', 'sc-actions');
            const save = node('button', '存入画册', 'sc-button');
            save.type = 'button';
            save.addEventListener('click', async () => {
              const saved = await run(form, () => api.console_save_image(reply.preview_id));
              if (saved?.ok) {
                save.disabled = true;
                save.textContent = saved.label;
              }
            });
            actions.append(save);
            out.append(actions);
          }
          return {...reply, label: kind === 'image' ? '测试图已生成' : '语音已生成'};
        }
        return reply.ok ? {ok: false, error: '样本格式无效'} : reply;
      });
      if (!result) out.replaceChildren();
      update();
    });
  }

  const serviceForms = `<div class="sc-service-forms" aria-label="本机服务">
          <form class="sc-form" data-console-service="image">
            <h4>生图 · ComfyUI</h4>
            <label class="sc-check"><input name="enabled" type="checkbox">启用</label>
            <label>本机地址<input name="address" type="url" maxlength="500" required></label>
            <div class="sc-actions"><button class="sc-button" type="button" data-service-test>验证接口</button>
              <button class="sc-button" type="submit">保存</button>
              <button class="sc-button" type="button" data-sample>试一张图</button></div>
            <p class="sc-feedback" role="status"></p>
            <div data-sample-output></div>
          </form>
          <form class="sc-form" data-console-service="voice">
            <h4>语音 · HTTP TTS</h4>
            <label class="sc-check"><input name="enabled" type="checkbox">启用</label>
            <label>本机地址<input name="address" type="url" maxlength="500" required></label>
            <label>试听文字<input name="sample" maxlength="80" value="你好，今天的声音听得清楚吗？"></label>
            <div class="sc-actions"><button class="sc-button" type="button" data-service-test>验证接口</button>
              <button class="sc-button" type="submit">保存</button>
              <button class="sc-button" type="button" data-sample>试听</button></div>
            <p class="sc-feedback" role="status"></p>
            <div data-sample-output></div>
          </form>
        </div>`;

  function attachServices(root, api, options) {
    for (const form of root.querySelectorAll('[data-console-service]')) {
      const kind = form.dataset.consoleService;
      if (options?.[kind]) attachService(form, kind, api, options[kind]);
    }
  }

  async function mountServices(root, api) {
    root.replaceChildren(node('p', '正在读取可选服务…', 'sc-caption'));
    try {
      const result = await api.console_view();
      if (!result?.ok) throw new Error();
      root.innerHTML = serviceForms;
      if (result.warning) root.prepend(node('p', result.warning, 'sc-caption'));
      attachServices(root, api, result.service_options);
    } catch {
      const retry = node('button', '重试读取可选服务', 'sc-button');
      retry.type = 'button';
      retry.onclick = () => mountServices(root, api);
      root.replaceChildren(node('p', '暂时读不到可选服务', 'sc-caption'), retry);
    }
  }

  function render(root, api, result) {
    root.innerHTML = `
      <section class="sc-chat" aria-labelledby="sc-chat-title">
        <h3 id="sc-chat-title">聊天连接</h3>
        <p class="sc-caption sc-current" aria-live="polite"></p>
        <form class="sc-form" data-console-chat>
          <div class="sc-grid">
            <label>连接类型<select name="mode">
              <option value="demo">离线演示</option>
              <option value="dsh_local">DSH 本地网关</option>
              <option value="dsh_remote">DSH HTTPS 网关</option>
            </select></label>
            <label data-chat-field>网关地址<input name="address" type="url" maxlength="500" required></label>
            <label data-chat-field>网关令牌<input name="token" type="password" maxlength="1024"
              autocomplete="new-password" spellcheck="false"></label>
          </div>
          <div class="sc-actions"><button class="sc-button" type="button" data-chat-test>测试连接</button>
            <button class="sc-button sc-primary" type="submit">保存连接</button></div>
          <p class="sc-feedback" role="status"></p>
        </form>
        <div data-console-diagnostics></div>
        <div data-console-data></div>
        <div class="sc-form sc-mobile" data-console-mobile hidden></div>
      </section>
      <section class="sc-services" aria-labelledby="sc-services-title">
        <h3 id="sc-services-title">服务</h3>
        ${serviceForms}
        <ul class="sc-status-list" hidden></ul>
      </section>
      <section class="sc-bridge" aria-labelledby="sc-bridge-title">
        <h3 id="sc-bridge-title">桥接控制台</h3>
        <p class="sc-caption" data-bridge-state></p>
        <a class="sc-button sc-link" target="_blank" rel="noopener noreferrer" hidden>打开桥接控制台</a>
        <form class="sc-form sc-bridge-form" data-console-bridge>
          <label>本机控制台地址<input name="address" type="url" maxlength="500" placeholder="http://127.0.0.1:端口"></label>
          <div class="sc-actions"><button class="sc-button" type="submit">保存入口</button></div>
          <p class="sc-feedback" role="status"></p>
        </form>
      </section>
      <section class="sc-billing" aria-labelledby="sc-billing-title">
        <h3 id="sc-billing-title">今天的账单</h3>
        <p class="sc-caption" data-console-billing></p>
      </section>`;

    const pageStatus = root.querySelector('.sc-current');
    const chat = root.querySelector('[data-console-chat]');
    const chatMode = chat.elements.mode;
    const token = chat.elements.token;
    const current = result.chat;
    const saveChat = chat.querySelector('[type="submit"]');
    const testChat = chat.querySelector('[data-chat-test]');
    const details = connectionDetails();
    const rebind = node('button', '重新绑定此网关', 'sc-button');
    rebind.type = 'button';
    rebind.hidden = true;
    rebind.dataset.chatRebind = '';
    const rebindFeedback = node('p', '', 'sc-feedback');
    rebindFeedback.setAttribute('role', 'status');
    let observedIdentity = '';
    let chatBusy = false;
    let rebinding = false;
    function updateActions() {
      const busy = chatBusy || rebinding;
      rebind.disabled = saveChat.disabled = busy;
      testChat.disabled = busy || chatMode.value === 'demo';
    }
    async function loadDiagnostic() {
      if (!rebinding) rebindFeedback.textContent = '';
      const value = await api.console_diagnostics();
      if (!value?.ok || (value.phase !== 'endpoint_changed' && !details.region.open)) return value;
      try {
        const profile = await api.profile();
        return {...value, profile: profile || {ok: false}};
      } catch {
        return {...value, profile: {ok: false}};
      }
    }
    const diag = diagnostics(loadDiagnostic, result.diagnostic, {
      actions: [rebind],
      onShow(value) {
        const mode = modeText(value?.mode || current.mode);
        const phase = phaseText(value);
        pageStatus.textContent = mode === phase ? mode : `${mode} · ${phase}`;
        const status = value?.profile?.ok && value.profile.status;
        const observed = status?.observed_endpoint_id;
        observedIdentity = status?.endpoint_changed === true && typeof observed === 'string' && observed.trim()
          ? observed : '';
        rebind.hidden = !observedIdentity;
        updateActions();
        details.show(value?.profile);
      },
    });
    diag.region.append(rebindFeedback, details.region);
    details.region.addEventListener('toggle', () => {
      if (details.region.open) diag.update();
      else details.show(null);
    });
    rebind.addEventListener('click', async () => {
      const observed = observedIdentity;
      if (!observed || chatBusy || rebinding || !window.confirm(
        '网关数据身份已改变。重新绑定会保留聊天历史，新网关使用独立接收游标。旧网关的待发消息不会转投。要重新绑定当前网关吗？'
      )) return;
      rebinding = true;
      updateActions();
      rebindFeedback.dataset.kind = '';
      rebindFeedback.textContent = '正在重新绑定…';
      try {
        const reply = await api.rebind_endpoint(observed);
        if (!reply?.ok) throw new Error(reply?.error || '');
        rebindFeedback.dataset.kind = 'ok';
        rebindFeedback.textContent = '已重新绑定，聊天历史已保留';
        await diag.update();
        window.dispatchEvent(new Event('panel-connection-changed'));
      } catch (error) {
        observedIdentity = '';
        rebind.hidden = true;
        rebindFeedback.dataset.kind = 'error';
        rebindFeedback.textContent = detailText(error.message) || '重新绑定未完成，请刷新诊断后重试';
      } finally {
        rebinding = false;
        updateActions();
      }
    });
    if (result.warning) formFeedback(chat).textContent = result.warning;
    root.querySelector('[data-console-diagnostics]').append(diag.region);
    if (result.data_path) {
      root.querySelector('[data-console-data]').append(dataRow(result.data_path,
        result.can_open_data ? () => api.console_open_data() : null));
    }
    if (window.MobileControls) {
      const mobile = root.querySelector('[data-console-mobile]');
      mobile.hidden = false;
      window.MobileControls.mount(mobile, api);
    }
    const bill = root.querySelector('[data-console-billing]');
    bill.textContent = result.billing?.source || '未接入账单数据源';
    chatMode.value = current.mode;
    chat.elements.address.value = current.base_url || '';
    function chatFields() {
      const demo = chatMode.value === 'demo';
      chat.querySelectorAll('[data-chat-field]').forEach(label => {
        label.hidden = demo;
        label.querySelector('input').disabled = demo;
      });
      const known = current.has_token && chatMode.value === current.mode &&
        chat.elements.address.value.replace(/\/+$/, '') === current.base_url;
      token.required = !demo && !known;
      token.placeholder = known ? '已保存 · 留空保留' : '';
      chat.elements.address.placeholder = chatMode.value === 'dsh_remote' ? 'https://网关地址' : 'http://127.0.0.1:端口';
      updateActions();
    }
    chatFields();
    chat.addEventListener('input', chatFields);
    const chatPatch = () => ({
      mode: chatMode.value, base_url: chat.elements.address.value, token: token.value || null,
    });
    chat.addEventListener('submit', async event => {
      event.preventDefault();
      if (chatBusy || rebinding) return;
      chatBusy = true;
      updateActions();
      const patch = chatPatch();
      try {
        const saved = await run(chat, async () => {
          const result = await api.console_chat(patch, 'save');
          return result.ok ? {...result, label: `已保存：${phaseText(result.diagnostic)}`} : result;
        });
        if (!saved?.ok) return;
        Object.assign(current, saved.chat);
        chatMode.value = current.mode;
        chat.elements.address.value = current.base_url;
        token.value = '';
        chatFields();
        rebindFeedback.textContent = '';
        await diag.update();
        window.dispatchEvent(new Event('panel-connection-changed'));
      } finally {
        chatBusy = false;
        updateActions();
      }
    });
    testChat.addEventListener('click', async () => {
      if (chatBusy || rebinding || !chat.reportValidity()) return;
      chatBusy = true;
      updateActions();
      try {
        const patch = chatPatch();
        await run(chat, async () => {
          const result = await api.console_chat(patch, 'test');
          return result.ok ? {...result, label: phaseText(result.diagnostic) + ' · 未更改连接'} : result;
        });
      } finally {
        chatBusy = false;
        updateActions();
      }
    });

    const bridge = root.querySelector('[data-console-bridge]');
    const shortcut = root.querySelector('.sc-bridge a');
    const bridgeState = root.querySelector('[data-bridge-state]');
    function showBridge(url) {
      bridge.elements.address.value = url || '';
      localLink(shortcut, url);
      bridgeState.textContent = shortcut.hidden ? '未配置控制台' : '已有入口 · 未验证在线状态';
    }
    showBridge(result.bridge);
    bridge.addEventListener('submit', async event => {
      event.preventDefault();
      const saved = await run(bridge, () => api.console_bridge(bridge.elements.address.value.trim()));
      if (saved?.ok) showBridge(saved.url);
    });

    if (result.editable) {
      attachServices(root, api, result.service_options);
    } else {
      for (const form of [chat, bridge, root.querySelector('.sc-service-forms')]) form.hidden = true;
      const list = root.querySelector('.sc-status-list');
      list.hidden = false;
      for (const service of result.services || []) {
        list.append(node('li', `${service.label} · ${phases[service.phase] || '状态未知'}`));
      }
      if (!list.childElementCount) list.append(node('li', '未配置可选服务'));
    }
    return diag.update;
  }

  async function mount(root, api) {
    if (!root) return;
    if (mounted.has(root)) {
      const refresh = mounted.get(root);
      if (refresh) await refresh();
      return;
    }
    mounted.set(root, null);
    root.classList.add('service-console');
    root.replaceChildren(node('p', '正在读取控制台…', 'sc-caption'));
    root.setAttribute('aria-busy', 'true');
    try {
      const data = await api.console_view();
      if (!data?.ok) throw new Error();
      const refresh = render(root, api, data);
      mounted.set(root, refresh);
      await refresh();
    } catch {
      mounted.delete(root);
      const retry = node('button', '重试', 'sc-button');
      retry.type = 'button';
      retry.onclick = () => mount(root, api);
      root.replaceChildren(node('p', '控制台暂时无法读取', 'sc-caption'), retry);
    } finally {
      root.removeAttribute('aria-busy');
    }
  }

  window.ServiceConsole = {mount, mountServices, diagnostics, fromProfile, dataRow};
})();
