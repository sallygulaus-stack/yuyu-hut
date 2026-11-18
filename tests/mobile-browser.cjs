/* Pair a phone-size browser with a separate desktop browser session. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright-core');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'panel-mobile-browser-'));
const screenshots = process.env.PANEL_TEST_SCREENSHOTS;
const data = path.join(temp, 'data');
const checks = [];
function pass(description) {
  checks.push(description);
  console.log('通过：' + description);
}

function wave() {
  const data = Buffer.alloc(44 + 3200);
  data.write('RIFF', 0);
  data.writeUInt32LE(data.length - 8, 4);
  data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(8000, 24);
  data.writeUInt32LE(16000, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write('data', 36);
  data.writeUInt32LE(data.length - 44, 40);
  return data;
}

(async () => {
  let service, browser;
  let stderr = '';
  try {
    fs.writeFileSync(path.join(temp, 'config.json'), '{}');
    service = spawn(process.env.PYTHON || 'python', ['-B', path.join(root, 'panel/panel.py'),
      '--demo', '--no-browser', '--config', path.join(temp, 'config.json'), '--data-dir', data],
    {cwd: temp, windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}});
    service.stderr.on('data', chunk => { stderr += chunk; });
    const home = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('演示启动超时：' + stderr)), 20000);
      readline.createInterface({input: service.stdout}).on('line', line => {
        try {
          const info = JSON.parse(line);
          if (info.url) { clearTimeout(timer); resolve(info.url); }
        } catch {}
      });
      service.once('error', error => { clearTimeout(timer); reject(error); });
      service.once('exit', () => { clearTimeout(timer); reject(new Error('演示提前退出：' + stderr)); });
    });
    const options = process.env.BROWSER_EXECUTABLE
      ? {executablePath: process.env.BROWSER_EXECUTABLE} : {channel: process.env.BROWSER_CHANNEL || 'msedge'};
    browser = await chromium.launch({headless: true, ...options});
    const deskContext = await browser.newContext({viewport: {width: 1366, height: 900}});
    const phoneContext = await browser.newContext({viewport: {width: 390, height: 780}, isMobile: true, hasTouch: true});
    const desk = await deskContext.newPage(), phone = await phoneContext.newPage();
    const errors = [], outside = [];
    for (const page of [desk, phone]) {
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if ((url.protocol === 'http:' && url.hostname === '127.0.0.1') ||
            ['data:', 'blob:'].includes(url.protocol)) return route.continue();
        outside.push(url.origin);
        return route.abort();
      });
    }
    let releaseInitialPhoto;
    const initialPhotoGate = new Promise(resolve => { releaseInitialPhoto = resolve; });
    await phone.route(/\/media\/image\/[^/]+\/[0-9a-f]{64}$/, async route => {
      await initialPhotoGate;
      await route.continue();
    }, {times: 1});
    const stream = phone.locator('#stream');
    const atLatest = () => phone.waitForFunction(() => {
      const stream = document.getElementById('stream');
      return stream.scrollHeight - stream.clientHeight - stream.scrollTop <= 1;
    }, null, {timeout: 4000});
    await desk.goto(home);
    await desk.waitForFunction(() => CHATS.yu.msgs.length >= 30);
    const desktop = (method, ...args) => desk.evaluate(([name, values]) => pywebview.api[name](...values), [method, args]);
    const blue = fs.readFileSync(path.join(root, 'panel/web/assets/default-me.png'));
    const pink = fs.readFileSync(path.join(root, 'panel/web/assets/default-peer.png'));
    assert((await desktop('profile_appearance', 'me', {display_name: '合成左侧称呼', avatar: pink.toString('base64')})).ok);
    assert((await desktop('profile_appearance', 'peer', {display_name: '合成右侧称呼', avatar: blue.toString('base64')})).ok);
    const gallery = (await desktop('gallery_list')).画?.[0];
    assert(gallery?.媒体ID, '演示画册应有独立合成图片');
    await desk.locator('[data-nav="service"]').click();
    const controls = desk.locator('[data-console-mobile]');
    await controls.getByText('未开启', {exact: true}).waitFor();
    const toggle = controls.locator('[data-mobile-toggle]');
    await toggle.click();
    await controls.getByText('等待手机配对', {exact: true}).waitFor();
    const address = await controls.locator('[data-mobile-url]').textContent();
    const code = await controls.locator('[data-mobile-code]').inputValue();
    assert.match(address, /^http:\/\/127\.0\.0\.1:\d+\/$/);
    assert.notEqual(new URL(address).port, new URL(home).port);
    assert.match(code, /^[A-Za-z0-9_-]{24}$/);
    pass('桌面入口默认关闭，可一键分配独立本机端口和配对码');

    const token = (await (await fetch(new URL('/api/bootstrap', home))).json()).session_token;
    async function rpc(method, value = {}, headers = {}) {
      return fetch(address + 'api/' + method, {method: 'POST',
        headers: {'Content-Type': 'application/json', ...headers}, body: JSON.stringify(value)});
    }
    assert.equal((await rpc('history')).status, 401);
    assert.equal((await rpc('history', {}, {Authorization: 'Bearer ' + token})).status, 401);
    assert.equal((await rpc('pair', {code}, {Origin: 'http://example.invalid'})).status, 403);
    await phone.goto(address);
    await phone.locator('#pair-code').fill('wrong');
    await phone.getByRole('button', {name: '配对', exact: true}).click();
    await phone.getByText('配对码无效或过期').waitFor();
    await phone.locator('#pair-code').fill(code);
    await phone.getByRole('button', {name: '配对', exact: true}).click();
    await phone.locator('#messages .msg').first().waitFor();
    assert.equal(await phone.locator('#peer-name').textContent(), '合成右侧称呼');
    assert.match(await phone.locator('#peer-avatar').getAttribute('src'), /^\/avatar\/peer\//);
    assert.match(await phone.locator('.mine .avatar').last().getAttribute('src'), /^\/avatar\/me\//);
    await phone.locator('#peer-avatar').evaluate(image => image.decode());
    assert((await phone.getByText('合成左侧称呼', {exact: true}).count()) > 0);
    assert.equal((await rpc('pair', {code})).status, 401);
    await controls.getByText('已配对', {exact: true}).waitFor();
    pass('手机配对码使用一次，桌面令牌不可替代，双方称呼和头像正确显示');

    const initialPhoto = phone.locator('.msg').last().locator('.photo img');
    assert.equal(await initialPhoto.evaluate(image => image.naturalHeight), 0);
    await atLatest();
    releaseInitialPhoto();
    await initialPhoto.evaluate(image => image.decode());
    await atLatest();
    pass('刚打开的手机聊天跟住最后一条迟到的图片');

    const oldRows = await phone.locator('.msg').count();
    assert.equal(oldRows, 30);
    await stream.evaluate(element => { element.scrollTop = 0; });
    const firstSequence = await phone.locator('.msg').first().getAttribute('data-sequence');
    const originalFirst = phone.locator(`.msg[data-sequence="${firstSequence}"]`);
    const anchorTop = await originalFirst.evaluate(element => element.getBoundingClientRect().top);
    await phone.locator('#older').click();
    await phone.waitForFunction(() => document.querySelectorAll('.msg').length === 34);
    assert.equal(await phone.locator('#older').isHidden(), true);
    const assertAnchor = async () => {
      const top = await originalFirst.evaluate(element => element.getBoundingClientRect().top);
      assert(Math.abs(top - anchorTop) <= 1, `上翻位置移动了 ${top - anchorTop} 像素`);
    };
    await assertAnchor();
    pass('手机有独立的历史翻页，上翻后正在看的消息留在原位');

    const phoneToken = await phone.evaluate(() => sessionStorage.getItem('fish-house-mobile-session'));
    const phoneHeaders = {Authorization: 'Bearer ' + phoneToken};
    for (const endpoint of ['bootstrap', 'console_view', 'gallery_list', 'diary_read', 'mobile_manage', 'chat_send_record']) {
      assert.equal((await rpc(endpoint, {}, phoneHeaders)).status, 404, endpoint);
    }
    for (const route of ['index.html', 'assets/default-me.png', 'download/' + gallery.媒体ID,
      'media/image/' + gallery.媒体ID + '/' + '0'.repeat(64)]) {
      assert.equal((await fetch(address + route)).status, 404, route);
    }
    pass('手机只开放聊天，画册原件及桌面、控制台、日记接口拒绝访问');

    await desk.locator('[data-nav="chat"]').click();
    await desk.locator('#ta').fill('合成桌面发往手机');
    await desk.locator('#sendBtn').click();
    await phone.getByText('合成桌面发往手机', {exact: true}).waitFor();
    await phone.getByText('演示回复：合成桌面发往手机', {exact: true}).waitFor();
    await assertAnchor();
    pass('翻看旧消息时，收到新消息仍停在原来的位置');
    await stream.evaluate(element => { element.scrollTop = element.scrollHeight; });
    await phone.locator('#text').fill('合成手机发往桌面');
    await phone.locator('#send').click();
    await desk.locator('#stream').getByText('演示回复：合成手机发往桌面', {exact: true}).waitFor();
    const history = await desktop('chat_history', 10);
    assert.equal(history.消息.filter(row => row.段.some(part => part.text === '合成手机发往桌面')).length, 1);
    pass('桌面和手机同步文字，各端只写入一条对应的发信');

    const draft = {name: 'synthetic.png', mimeType: 'image/png', buffer: blue};
    await phone.locator('#text').fill('合成图片草稿');
    await phone.locator('#file').setInputFiles(draft);
    await phone.locator('#draft').getByText('synthetic.png').waitFor();
    await phone.locator('#remove').click();
    assert.equal(await phone.locator('#draft').isHidden(), true);
    assert.equal(await phone.locator('#text').inputValue(), '合成图片草稿');
    await phone.locator('#file').setInputFiles(draft);
    await phone.locator('#send').click();
    await desk.locator('#stream').getByText('合成图片草稿', {exact: true}).waitFor();
    const sentPhoto = phone.locator('.msg').filter({hasText: '合成图片草稿'}).getByRole('button', {name: '查看聊天图片'});
    await sentPhoto.click();
    const preview = phone.locator('#full-image');
    await preview.evaluate(image => image.decode());
    await phone.getByRole('button', {name: '关闭图片'}).click();
    const mediaUrl = await sentPhoto.locator('img').getAttribute('src');
    assert.deepEqual(Buffer.from(await (await fetch(new URL(mediaUrl, address))).arrayBuffer()), blue);
    pass('手机可以暂存、移除、重新选择图片草稿，发送给桌面并查看原图');

    await stream.evaluate(element => { element.scrollTop = element.scrollHeight; });
    const wav = wave();
    assert((await desktop('chat_send_record', wav.toString('base64'), 'synthetic.wav', '合成桌面语音')).ok);
    const audio = phone.locator('.msg').filter({hasText: '合成桌面语音'}).locator('audio');
    await audio.waitFor();
    const audioUrl = await audio.getAttribute('src');
    const range = await fetch(new URL(audioUrl, address), {headers: {Range: 'bytes=0-43'}});
    assert.equal(range.status, 206);
    assert.deepEqual(Buffer.from(await range.arrayBuffer()), wav.subarray(0, 44));
    assert(await audio.evaluate(element => {
      element.load();
      return new Promise((resolve, reject) => {
        element.addEventListener('loadedmetadata', () => resolve(element.duration > 0), {once: true});
        element.addEventListener('error', reject, {once: true});
      });
    }));
    for (const width of [390, 320]) {
      await phone.setViewportSize({width, height: 740});
      await atLatest();
      assert(await phone.locator('#text').evaluate(element => {
        const box = element.getBoundingClientRect();
        return box.x >= 0 && box.right <= innerWidth && box.bottom <= innerHeight &&
          document.documentElement.scrollWidth === innerWidth;
      }));
      assert(await audio.evaluate(element => {
        const audio = element.getBoundingClientRect();
        const bounds = document.getElementById('stream').getBoundingClientRect();
        return audio.top >= bounds.top && audio.bottom <= bounds.bottom;
      }));
    }
    if (screenshots) {
      fs.mkdirSync(screenshots, {recursive: true});
      await phone.screenshot({path: path.join(screenshots, 'mobile-chat-320.png')});
      await phone.setViewportSize({width: 390, height: 780});
      await atLatest();
      await phone.screenshot({path: path.join(screenshots, 'mobile-chat-390.png')});
    }
    pass('手机读取桌面语音，支持分段播放，320 与 390 像素宽时可同屏看见最新语音和输入栏');

    await phone.reload();
    await phone.getByText('合成桌面语音', {exact: true}).waitFor();
    await desk.locator('[data-nav="service"]').click();
    await controls.locator('[data-mobile-renew]').click();
    await phone.locator('#pair').waitFor({state: 'visible'});
    assert.equal((await rpc('history', {}, phoneHeaders)).status, 401);
    assert.equal((await fetch(new URL(mediaUrl, address))).status, 404);
    assert.equal((await fetch(new URL(audioUrl, address))).status, 404);
    const second = await controls.locator('[data-mobile-code]').inputValue();
    assert.notEqual(second, code);
    await phone.locator('#pair-code').fill(second);
    await phone.getByRole('button', {name: '配对', exact: true}).click();
    await phone.locator('#leave').waitFor({state: 'visible'});
    await toggle.click();
    await controls.getByText('未开启', {exact: true}).waitFor();
    await assert.rejects(fetch(address));
    pass('刷新保留当前配对，重新配对吊销旧访问，关闭后端口停止监听');

    assert.deepEqual(errors, []);
    assert.deepEqual(outside, []);
    pass('网页无脚本错误，无外部访问');
    console.log(JSON.stringify({passed: checks.length, errors, outside, screenshots}, null, 2));
  } finally {
    if (browser) await browser.close();
    if (service && service.exitCode === null) {
      const stopped = new Promise(resolve => service.once('close', resolve));
      service.kill();
      await stopped;
    }
    assert.equal(path.dirname(temp), path.resolve(os.tmpdir()));
    fs.rmSync(temp, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
