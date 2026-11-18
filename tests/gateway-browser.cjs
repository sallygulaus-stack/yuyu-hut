/* Actual QQ-style panel against a loopback or SSH-forwarded test gateway. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright-core');
const root = path.resolve(__dirname, '..');
const configPath = process.argv[2];
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const connection = config[config.mode];
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-browser-'));
const screenshots = process.env.PANEL_TEST_SCREENSHOTS || path.join(temp, 'screenshots');
let process_, browser, page;
const checks = [];
const network = [];
function check(name, value) {
  assert.ok(value, name);
  checks.push(name);
  console.log('PASS ' + name);
}
function request(route, body) {
  const url = new URL(connection.base_url + route);
  const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const req = (url.protocol === 'https:' ? https : http).request(url, {
      method: payload ? 'POST' : 'GET', ca: connection.ca_file ? fs.readFileSync(connection.ca_file) : undefined,
      headers: {Authorization: 'Bearer ' + connection.token, 'Content-Type': 'application/json',
        ...(payload ? {'Content-Length': payload.length} : {})}
    }, res => {
      let data = '';
      res.on('data', x => { data += x; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (error) { reject(error); }
      });
    });
    req.on('error', reject);
    req.setTimeout(5000, () => req.destroy(new Error('gateway timeout')));
    req.end(payload);
  });
}
async function startPanel() {
  process_ = spawn(process.env.PYTHON || 'python', ['-B', path.join(root, 'panel/panel.py'),
    '--config', configPath, '--data-dir', path.join(temp, 'panel'), '--no-browser'],
    {cwd: temp, windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}});
  let stderr = '';
  process_.stderr.on('data', data => { stderr += data; });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('panel startup: ' + stderr)), 30000);
    readline.createInterface({input: process_.stdout}).on('line', line => {
      try {
        const value = JSON.parse(line);
        if (value.url) { clearTimeout(timer); resolve(value.url); }
      } catch (_) {}
    });
    process_.once('error', reject);
  });
}
async function stopPanel() {
  if (process_ && process_.exitCode === null) {
    const done = new Promise(resolve => process_.once('close', resolve));
    process_.kill();
    await done;
  }
}
async function history(page) {
  return page.evaluate(() => window.pywebview.api.chat_history(100));
}
function trackAssets(page) {
  const asset = request => ['script', 'stylesheet'].includes(request.resourceType());
  const assetPath = request => decodeURIComponent(new URL(request.url()).pathname);
  page.on('request', request => {
    if (asset(request)) network.push([assetPath(request), 'requested']);
  });
  page.on('response', response => {
    if (asset(response.request())) network.push([assetPath(response.request()), response.status()]);
  });
  page.on('requestfailed', request => {
    if (asset(request)) network.push([assetPath(request), request.failure()?.errorText]);
  });
}
(async () => {
  try {
    fs.mkdirSync(screenshots, {recursive: true});
    const address = await startPanel();
    browser = await chromium.launch(process.env.BROWSER_EXECUTABLE
      ? {headless: true, executablePath: process.env.BROWSER_EXECUTABLE}
      : {headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge'});
    page = await browser.newPage({viewport: {width: 1366, height: 900}});
    trackAssets(page);
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('PAGE_ERROR', error.stack); });
    let releaseProfile;
    const delayedProfile = new Promise(resolve => { releaseProfile = resolve; });
    await page.route('**/assets/profile.js', async route => {
      await delayedProfile;
      await route.continue();
    }, {times: 1});
    try {
      await page.goto(address, {waitUntil: 'commit'});
      await page.waitForFunction(() => typeof 切功能 === 'function');
      assert.equal(await page.evaluate(() => typeof window.打开资料), 'undefined');
      await page.locator('#meAvatar').click();
    } finally {
      releaseProfile();
    }
    try {
      await page.waitForFunction(async () => {
        try { return (await window.pywebview.api.chat_ready()).connected; }
        catch { return false; }
      });
    } catch (error) {
      console.error('INITIAL_CONNECT_STATE', await page.evaluate(() => window.pywebview.api.chat_ready()));
      throw error;
    }
    await page.waitForFunction(() => typeof window.顶栏状态 === 'function');
    await page.evaluate(() => 拉资料());
    await page.locator('#profileName').getByText('Synthetic User', {exact:true}).waitFor();
    check('avatar clicked during script load opens when ready', await page.locator('#profileMask').isVisible());
    const profile = await page.evaluate(() => window.pywebview.api.profile());
    check('my card uses the profile associated with the OneBot peer ID', profile.me.identity === '20001');
    await page.locator('#profilePeerTab').click();
    await page.locator('#profileName').getByText('Synthetic Companion', {exact:true}).waitFor();
    check('peer card uses the profile associated with the OneBot self ID', profile.peer.identity === '20000');
    await page.locator('#profileClose').click();
    check('profile and bubble avatar sources agree', await page.evaluate(() =>
      document.querySelector('.rail .me > img').getAttribute('src') === ME_AV &&
      document.querySelector('.chat-head .av').getAttribute('src') === HER_AV));
    await page.evaluate(() => window.顶栏状态({phase: 'degraded', 通: true, 连接: 1}));
    check('degraded is amber even with a connected transport', await page.evaluate(() =>
      document.querySelector('#tbDot').classList.contains('warn') &&
      !document.querySelector('#tbDot').classList.contains('on')));
    await page.evaluate(async () => window.顶栏状态(await window.pywebview.api.chat_ready()));
    const text = 'browser synthetic ' + Date.now();
    await page.locator('#ta').fill(text);
    await page.locator('#sendBtn').click();
    await page.waitForFunction(text => CHATS.yu.msgs.some(m => m.text === 'synthetic reply: ' + text), text);
    check('text round trip from actual panel', true);
    // Tiny PNG generated by a browser canvas, unrelated to user media.
    const png = await page.evaluate(() => {
      const c = document.createElement('canvas'); c.width = 64; c.height = 48;
      const x = c.getContext('2d'); x.fillStyle = '#23a57e'; x.fillRect(0, 0, 64, 48);
      return c.toDataURL('image/png').split(',')[1];
    });
    const priorImages = (await history(page)).消息.filter(r => r.谁 === '她' && r.段.some(s => s.type === 'image')).length;
    await page.locator('#fileInput').setInputFiles({name: 'synthetic.png', mimeType: 'image/png',
      buffer: Buffer.from(png, 'base64')});
    await page.waitForFunction(() => 待发图 !== null);
    await page.locator('#sendBtn').click();
    await page.waitForFunction(async count => {
      try {
        const h = await window.pywebview.api.chat_history(100);
        return h.消息.filter(r => r.谁 === '她' && r.段.some(s => s.type === 'image')).length > count;
      } catch { return false; }
    }, priorImages);
    await page.waitForFunction(() => [...document.querySelectorAll('.msg.her img.pic')].some(im => im.naturalWidth > 0));
    check('image returned without shared client disk', true);
    const wav = Buffer.alloc(44 + 3200);
    wav.write('RIFF', 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
    wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
    wav.writeUInt32LE(8000, 24); wav.writeUInt32LE(16000, 28); wav.writeUInt16LE(2, 32);
    wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(3200, 40);
    for (let i = 0; i < 1600; i++) wav.writeInt16LE(Math.round(Math.sin(i * .35) * 2000), 44 + i * 2);
    const priorVoices = (await history(page)).消息.filter(r => r.谁 === '她' && r.段.some(s => s.type === 'record')).length;
    await page.locator('#audioFile').setInputFiles({name: 'synthetic.wav', mimeType: 'audio/wav', buffer: wav});
    await page.waitForFunction(async count => {
      try {
        const h = await window.pywebview.api.chat_history(100);
        return h.消息.filter(r => r.谁 === '她' && r.段.some(s => s.type === 'record')).length > count;
      } catch { return false; }
    }, priorVoices);
    await page.waitForFunction(() => [...声音们].some(a => a.readyState >= 1 && a.duration > 0));
    await page.locator('.msg.her .voice').last().click();
    check('voice bytes render as playable audio', true);
    for (const selector of ['[aria-label="语音通话"]', '[aria-label="视频通话"]', '#tShot', '#tFile', '#tVoice']) {
      check('visible atmosphere control ' + selector, await page.locator(selector).isVisible());
    }
    await page.waitForTimeout(600);
    const beforeAtmosphere = (await history(page)).总;
    await page.locator('[aria-label="语音通话"]').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('未呼叫远端'));
    await page.waitForTimeout(400);
    check('atmosphere does not forge a delivered message', (await history(page)).总 === beforeAtmosphere);
    await request('/__test/faults', {drop_receipt: true, fail_ack: true, duplicate_events: true});
    const uncertain = 'uncertain browser ' + Date.now();
    await page.locator('#ta').fill(uncertain);
    await page.locator('#sendBtn').click();
    await page.waitForFunction(() => !!document.querySelector('.msg.me.failed'));
    await page.locator('.msg.me.failed').last().click();
    await page.waitForFunction(async text => {
      try {
        const h = await window.pywebview.api.chat_history(100);
        return h.消息.some(r => r.谁 === '我' && r.段.some(s => s.text === text) &&
          ['gateway_accepted', 'framework_waiting', 'framework_unknown'].includes(r.delivery_status));
      } catch { return false; }
    }, uncertain);
    await page.waitForFunction(() => !document.querySelector('.msg.me.failed'));
    await request('/__test/faults', {fail_ack: false, duplicate_events: false});
    await page.waitForFunction(text => CHATS.yu.msgs.some(m => m.text === 'synthetic reply: ' + text), uncertain);
    const all = await history(page);
    check('lost receipt retried with stable identity',
      all.消息.filter(r => r.谁 === '我' && r.段.some(s => s.text === uncertain)).length === 1);
    check('replayed events shown once',
      all.消息.filter(r => r.谁 === '她' && r.段.some(s => s.text === 'synthetic reply: ' + uncertain)).length === 1);
    await page.screenshot({path: path.join(screenshots, 'gateway-desktop.png')});
    await page.close();
    await stopPanel();
    const restarted = await startPanel();
    page = await browser.newPage({viewport: {width: 1366, height: 900}});
    trackAssets(page);
    page.on('pageerror', error => { errors.push(error.message); console.error('PAGE_ERROR', error.stack); });
    await page.goto(restarted);
    await page.waitForFunction(async () => {
      try { return (await window.pywebview.api.chat_ready()).connected; }
      catch { return false; }
    });
    await page.waitForTimeout(700);
    check('client restart preserves history and receive cursor', (await history(page)).总 === all.总);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(screenshots, 'gateway-mobile.png')});
    check('QQ input fits mobile viewport', await page.locator('#ta').evaluate(n => {
      const b = n.getBoundingClientRect(); return b.x >= 0 && b.right <= innerWidth && b.bottom <= innerHeight;
    }));
    check('browser assets and scripts have no errors', errors.length === 0 &&
      network.every(([, outcome]) => outcome === 'requested' || (typeof outcome === 'number' && outcome < 400)));
    console.log(JSON.stringify({passed: checks.length, messages: all.总, errors,
      transport: new URL(connection.base_url).protocol, screenshots}));
  } catch (error) {
    console.error('ASSET_REQUESTS', JSON.stringify(network));
    try {
      if (page && !page.isClosed()) {
        const diagnostics = process.env.PANEL_TEST_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-browser-failure-'));
        await page.screenshot({path: path.join(diagnostics, 'failure.png'), timeout: 5000});
        const snapshot = await page.evaluate(async () => ({
          history: await window.pywebview.api.chat_history(100),
          state: await window.pywebview.api.chat_ready(), toast: document.querySelector('#toast').textContent
        }));
        fs.writeFileSync(path.join(diagnostics, 'failure.json'), JSON.stringify({network, ...snapshot}, null, 2));
        console.error('BROWSER_DIAGNOSTICS', diagnostics);
      }
    } catch {}
    throw error;
  } finally {
    if (browser) await browser.close();
    await stopPanel();
    assert.equal(path.dirname(temp), path.resolve(os.tmpdir()));
    fs.rmSync(temp, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
