/* First-use console with isolated DSH and media endpoints. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {setTimeout: pause} = require('node:timers/promises');
const {chromium} = require('playwright-core');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'yuyu-console-browser-'));
const screenshots = process.env.PANEL_TEST_SCREENSHOTS || path.join(temp, 'screenshots');
const testAddress = process.env.SYNTHETIC_GATEWAY_URL;
const mediaAddress = process.env.SYNTHETIC_MEDIA_URL;
const testToken = process.env.SYNTHETIC_GATEWAY_TOKEN;
let service, browser;

(async () => {
  assert(testAddress && mediaAddress && testToken, 'Start the isolated fixture via test_console.py --browser');
  let stderr = '';
  service = spawn(process.env.PYTHON || 'python', ['-B', path.join(root, 'panel/panel.py'),
    '--no-browser', '--data-dir', path.join(temp, 'data')], {
    cwd: temp, windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'},
  });
  service.stderr.on('data', data => { stderr += data; });
  const home = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Panel did not start: ' + stderr)), 15000);
    readline.createInterface({input: service.stdout}).on('line', line => {
      try {
        const payload = JSON.parse(line);
        if (payload.url) { clearTimeout(timer); resolve(payload.url); }
      } catch {}
    });
    service.once('error', error => { clearTimeout(timer); reject(error); });
  });
  browser = await chromium.launch(process.env.BROWSER_EXECUTABLE
    ? {headless: true, executablePath: process.env.BROWSER_EXECUTABLE}
    : {headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge'});
  const context = await browser.newContext({viewport: {width: 1366, height: 900},
    permissions: ['clipboard-read', 'clipboard-write']});
  const page = await context.newPage();
  const errors = [];
  const rebindCalls = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/rebind_endpoint') rebindCalls.push(request.postDataJSON());
  });
  await page.goto(home);
  await page.locator('[data-nav="service"]').click();
  const panel = page.locator('#svcList');
  for (const name of ['聊天连接', '服务', '桥接控制台', '今天的账单']) {
    await panel.getByRole('heading', {name, exact: true}).waitFor();
  }
  await panel.getByText('未接入账单数据源').waitFor();
  assert.equal(await panel.getByText('虚拟机桌面', {exact: true}).count(), 0);
  const chat = panel.locator('[data-console-chat]');
  const statusLine = panel.locator('.sc-current');
  const rebind = panel.locator('[data-chat-rebind]');
  await rebind.waitFor({state: 'hidden'});
  await chat.getByLabel('连接类型').selectOption('dsh_local');
  await chat.getByLabel('网关地址').fill(testAddress);
  await chat.getByLabel('网关令牌').fill(testToken);
  await chat.getByRole('button', {name: '测试连接'}).click();
  await chat.getByText('已连接 · 未更改连接', {exact: true}).waitFor();
  assert.equal((await page.evaluate(() => pywebview.api.profile())).mode, 'demo');
  await chat.getByRole('button', {name: '保存连接'}).click();
  await chat.getByText('已保存：已连接', {exact: true}).waitFor();
  assert.equal(await chat.getByLabel('网关令牌').inputValue(), '');
  const details = panel.locator('[data-console-connection-details]');
  const detailValues = () => details.locator('dl').evaluate(list => Object.fromEntries(
    [...list.querySelectorAll('dt')].map(term => [term.textContent, term.nextElementSibling.textContent])));
  const copyDiagnostic = async () => {
    await panel.getByRole('button', {name: '复制脱敏诊断'}).click();
    await panel.getByText('已复制', {exact: true}).waitFor();
    const value = await page.evaluate(() => navigator.clipboard.readText());
    assert.deepEqual(value.split('\n').map(line => line.split('：', 1)[0]), ['连接类型', '连接状态', '消息能力']);
    assert(value.includes('DSH 本地网关') && !value.includes(testAddress) && !value.includes(testToken));
    assert(!value.includes(temp) && !value.includes('20000') && !value.includes('20001'));
    return value;
  };
  const summary = details.locator('summary');
  assert.equal(await summary.textContent(), '连接诊断');
  assert.equal(await details.evaluate(element => element.open), false);
  await summary.click();
  await details.getByText('协议版本', {exact: true}).waitFor();
  const normalDetail = await detailValues();
  assert.equal(normalDetail['我的 ID'], '20001');
  assert.equal(normalDetail['对方 ID'], '20000');
  assert.equal(normalDetail.对方资料来源, 'DSH');
  assert.equal(normalDetail.对方角色, 'test-framework');
  assert.equal(normalDetail.协议版本, 'dsh-client/1');
  assert.equal(normalDetail.端版本, 'onebot-gateway/1');
  assert.match(normalDetail.连接能力, /图片、语音文件/);
  assert.match(normalDetail.连接端点, /DSH 本地连接/);
  assert.equal(normalDetail.所在位置, '本机');
  assert(!JSON.stringify(normalDetail).includes(testAddress) && !JSON.stringify(normalDetail).includes(testToken));
  await copyDiagnostic();
  await summary.click();

  for (const kind of ['image', 'voice']) {
    const form = panel.locator(`[data-console-service="${kind}"]`);
    await form.getByLabel('启用').check();
    await form.getByLabel('本机地址').fill(mediaAddress);
    await form.getByRole('button', {name: '保存', exact: true}).click();
    await form.getByText(/已保存/).waitFor();
  }
  const voice = panel.locator('[data-console-service="voice"]');
  await voice.getByRole('button', {name: '试听', exact: true}).click();
  await voice.getByText('语音已生成', {exact: true}).waitFor();
  await page.waitForFunction(() => document.querySelector('.sc-sample[controls]')?.duration > 0);
  const image = panel.locator('[data-console-service="image"]');
  await image.getByRole('button', {name: '试一张图'}).click();
  await image.getByRole('img', {name: 'ComfyUI 测试图'}).evaluate(img => img.decode());
  assert.equal((await page.evaluate(() => pywebview.api.gallery_list())).总, 0);
  await image.getByRole('button', {name: '存入画册'}).click();
  await image.getByRole('button', {name: '已存入画册', exact: true}).waitFor();
  assert.equal((await page.evaluate(() => pywebview.api.gallery_list())).总, 1);
  await image.getByRole('button', {name: '试一张图'}).click();
  await image.getByRole('button', {name: '存入画册'}).click();
  await image.getByRole('button', {name: '已在画册', exact: true}).waitFor();
  assert.equal((await page.evaluate(() => pywebview.api.gallery_list())).总, 1);
  fs.mkdirSync(screenshots, {recursive: true});
  await page.screenshot({path: path.join(screenshots, 'console-desktop.png')});
  for (const width of [390, 360]) {
    await page.setViewportSize({width, height: 640});
    await page.locator('#panel').evaluate(node => { node.scrollTop = 0; });
    assert(await panel.evaluate(container => [...container.querySelectorAll('button,input,select,code')].every(el => {
      const box = el.getBoundingClientRect();
      return !el.getClientRects().length || box.left >= 0 && box.right <= innerWidth + 1;
    })), 'Controls must fit at ' + width);
    await page.screenshot({path: path.join(screenshots, `console-${width}.png`)});
  }
  await page.getByRole('button', {name: '回到聊天', exact: true}).click();
  await page.locator('#ta').fill('console browser test');
  await page.locator('#sendBtn').click();
  await page.waitForFunction(() => CHATS.yu.msgs.some(row => row.text === 'synthetic reply: console browser test'));
  const ready = () => page.evaluate(() => pywebview.api.chat_ready());
  const waitReady = async predicate => {
    const deadline = performance.now() + 25000;
    let state;
    do {
      state = await ready();
      if (predicate(state)) return state;
      await pause(200);
    } while (performance.now() < deadline);
    assert.fail(`Expected gateway state not reached; last phase ${state?.phase}`);
  };
  const before = await ready();
  assert.equal(before.phase, 'connected');
  assert(before.bound_endpoint_id);
  const replaceGateway = async () => {
    const response = await fetch(`${mediaAddress}/replace-gateway`, {method: 'POST', body: '{}'});
    const {endpoint_id: identity} = await response.json();
    assert(identity);
    await waitReady(state => state.endpoint_changed && state.observed_endpoint_id === identity);
    return identity;
  };

  const firstIdentity = await replaceGateway();
  // Reopening the console must catch the change without an explicit refresh.
  await page.locator('[data-nav="service"]').click();
  await rebind.waitFor({state: 'visible'});
  assert.match(await statusLine.textContent(), /身份已变化/);
  await summary.click();
  await details.getByText(firstIdentity, {exact: true}).waitFor();
  const changedDetail = await detailValues();
  assert.equal(changedDetail.原网关身份, before.bound_endpoint_id);
  assert.match(changedDetail.错误详情, /网关数据身份已改变/);
  assert(await details.locator('dl').evaluate(list => [...list.children].every(row => {
    const bounds = row.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= innerWidth + 1 && row.scrollWidth <= row.clientWidth + 1;
  })), 'Expanded diagnostics must fit at 360px');
  await details.scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(screenshots, 'console-diagnostics-360.png')});
  const changedCopy = await copyDiagnostic();
  assert(!changedCopy.includes(firstIdentity) && !changedCopy.includes(before.bound_endpoint_id));

  let warning = '';
  page.once('dialog', async dialog => {
    warning = dialog.message();
    await dialog.dismiss();
  });
  await rebind.click();
  assert.match(warning, /保留聊天历史.*独立接收游标.*待发消息不会转投/);
  assert.deepEqual(rebindCalls, []);
  assert.equal((await ready()).bound_endpoint_id, before.bound_endpoint_id);

  const secondIdentity = await replaceGateway();
  assert.notEqual(firstIdentity, secondIdentity);
  page.once('dialog', dialog => dialog.accept());
  await rebind.click();
  await panel.getByText('网关身份已再次变化，请刷新诊断后重试', {exact: true}).waitFor();
  assert.deepEqual(rebindCalls, [[firstIdentity]]);
  assert.equal((await ready()).bound_endpoint_id, before.bound_endpoint_id);

  await panel.locator('[data-chat-diagnose]').click();
  await rebind.waitFor({state: 'visible'});
  await details.getByText(secondIdentity, {exact: true}).waitFor();
  await page.evaluate(() => {
    window.rebindConnectionChanged = false;
    window.addEventListener('panel-connection-changed', () => { window.rebindConnectionChanged = true; }, {once: true});
  });
  page.once('dialog', dialog => dialog.accept());
  await rebind.click();
  await panel.getByText('已重新绑定，聊天历史已保留', {exact: true}).waitFor();
  await page.waitForFunction(() => window.rebindConnectionChanged);
  await waitReady(state => state.connected && state.bound_endpoint_id === secondIdentity);
  assert.deepEqual(rebindCalls, [[firstIdentity], [secondIdentity]]);
  await rebind.waitFor({state: 'hidden'});
  await panel.locator('[data-chat-diagnose]').click();
  await page.waitForFunction(() => document.querySelector('.sc-current').textContent === 'DSH 本地网关 · 已连接');
  await page.getByRole('button', {name: '回到聊天', exact: true}).click();
  await page.locator('#ta').fill('console after rebinding');
  await page.locator('#sendBtn').click();
  await page.waitForFunction(() => CHATS.yu.msgs.some(row => row.text === 'synthetic reply: console after rebinding'));
  assert(await page.evaluate(() => CHATS.yu.msgs.some(row => row.text === 'synthetic reply: console browser test')));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({console_browser: 'PASS', layout_widths: [1366, 390, 360], gallery_items: 1,
    diagnostics_details: 'PASS', gateway_rebind: 'PASS', screenshots}));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (service) {
    await new Promise(resolve => {
      if (service.exitCode !== null) return resolve();
      service.once('exit', resolve);
      service.kill();
    });
  }
});
