/* Test the shipped browser transport against a real, isolated demo process. */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('playwright-core');
const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'panel-demo-browser-'));
const screenshots = process.env.PANEL_TEST_SCREENSHOTS || path.join(temp, 'screenshots');
const checks = [];
function check(name, value) {
  assert.ok(value, name);
  checks.push(name);
  console.log('通过：' + name);
}

function testWave(seconds) {
  const data = Buffer.alloc(44 + 16000 * seconds);
  data.write('RIFF', 0); data.writeUInt32LE(data.length - 8, 4); data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16); data.writeUInt16LE(1, 20); data.writeUInt16LE(1, 22);
  data.writeUInt32LE(8000, 24); data.writeUInt32LE(16000, 28); data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34); data.write('data', 36); data.writeUInt32LE(data.length - 44, 40);
  return data;
}

async function checkSongs(page, dataDir) {
  const open = () => page.locator('[data-feature="sing"]').click();
  const picker = page.locator('#songImportFiles');
  const status = page.locator('#songImportStatus');
  const wav1 = testWave(8), wav2 = testWave(12);
  const name = '合成 #50%';
  const file = buffer => ({name: name + '.wav', mimeType: 'audio/wav', buffer});
  const done = () => status.getByText('已导入 1 首。', {exact:true}).waitFor();
  await open();
  await page.getByRole('button', {name:'导入歌曲'}).waitFor();
  await page.locator('#stName').getByText('还没收歌', {exact:true}).waitFor();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', {name:'导入歌曲'}).click();
  await (await chooser).setFiles(file(wav1));
  await done();
  await page.locator('#stPlay').click();
  const current = () => page.locator('#stAudio').evaluate(audio =>
    ({src:audio.currentSrc, time:audio.currentTime, paused:audio.paused, duration:audio.duration}));
  await page.waitForFunction(() => document.querySelector('#stAudio').currentTime > 0.1);
  const before = await current();
  await picker.setInputFiles(file(wav2));
  await done();
  const after = await current();
  assert.equal(after.src, before.src);
  assert.ok(!after.paused && after.time >= before.time);
  const directory = path.join(dataDir, 'songs', 'imported');
  assert.deepEqual(fs.readFileSync(path.join(directory, name + '.wav')), wav1);
  assert.deepEqual(fs.readFileSync(path.join(directory, name + ' (2).wav')), wav2);
  const invalidName = await page.evaluate(encoded => window.pywebview.api.song_import('../invalid.wav', encoded),
    wav1.toString('base64'));
  assert.equal(invalidName.ok, false);
  await picker.setInputFiles(file(Buffer.alloc(32 * 1024 * 1024 + 1)));
  await status.getByText('单首歌曲不能超过 32 MB。', {exact:true}).waitFor();
  check('空歌单可导入，同名原件分别保存，超限和非法文件名拒绝，当前曲目继续播放', true);

  await page.reload();
  await open();
  await page.locator('#stListBtn').click();
  await page.getByRole('button', {name:`播放《${name} (2)》`, exact:true}).click();
  await page.waitForFunction(() => document.querySelector('#stAudio').currentTime > 0.1);
  check('刷新后能播放导入的同名曲目', (await current()).duration === 12);
}

(async () => {
  let service, browser;
  let stderr = '';
  try {
    service = spawn(process.env.PYTHON || 'python', ['-B', path.join(root, 'panel/panel.py'),
      '--demo', '--no-browser', '--data-dir', path.join(temp, 'data')], {
      cwd: temp, windowsHide: true, env: {...process.env, PYTHONIOENCODING: 'utf-8'}
    });
    service.stderr.on('data', data => { stderr += data; });
    const address = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('演示启动超时：' + stderr)), 30000);
      readline.createInterface({input: service.stdout}).on('line', line => {
        try {
          const info = JSON.parse(line);
          if (info.url) { clearTimeout(timer); resolve(info.url); }
        } catch (_) {}
      });
      service.once('error', error => { clearTimeout(timer); reject(error); });
      service.once('exit', () => { clearTimeout(timer); reject(new Error('演示提前退出：' + stderr)); });
    });
    const launch = {headless: true};
    if (process.env.BROWSER_EXECUTABLE) launch.executablePath = process.env.BROWSER_EXECUTABLE;
    else launch.channel = process.env.BROWSER_CHANNEL || 'msedge';
    browser = await chromium.launch(launch);
    const page = await browser.newPage({viewport: {width: 1366, height: 900}});
    const errors = [], external = [], failed = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.protocol === 'http:' && url.hostname === '127.0.0.1') return route.continue();
      if (['data:', 'blob:'].includes(url.protocol)) return route.continue();
      external.push(url.origin);
      return route.abort();
    });
    page.on('response', response => {
      if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) failed.push(response.status());
    });
    await page.goto(address);
    await page.waitForFunction(() => 聊天.总 === 34 && CHATS.yu.msgs.length >= 30);
    await page.waitForFunction(() => document.querySelector('#chatSub').textContent.includes('演示'));
    check('真实演示页面加载历史和连接状态', await page.title() === '鱼鱼小窝');
    const logo = page.locator('.topbar .tb-logo');
    await logo.evaluate(image => image.decode());
    const iconUrl = await page.locator('link[rel="icon"]').getAttribute('href');
    const icon = await page.request.get(new URL(iconUrl, address).href);
    check('顶栏和标签使用独立应用图标', await logo.getAttribute('src') === 'assets/app-icon.png' &&
      await logo.evaluate(image => image.naturalWidth === 96 && image.naturalHeight === 96) &&
      iconUrl === 'assets/app-icon.ico' && icon.ok() &&
      (await icon.body()).equals(fs.readFileSync(path.join(root, 'panel/web/assets/app-icon.ico'))));
    fs.mkdirSync(screenshots, {recursive: true});
    await page.locator('#meAvatar').press('Enter');
    await page.waitForFunction(() => document.querySelector('#profileMask').classList.contains('on') &&
      document.querySelector('#profileName').textContent === '我');
    const dialog = page.locator('#profileMask');
    const nickname = page.locator('#profileName');
    const presence = page.locator('#profilePresence');
    const edit = page.locator('#profileEdit');
    const focus = () => page.evaluate(() => document.activeElement.id);
    check('我的名片只显示个人资料，键盘可打开', await dialog.getByRole('tab').count() === 2 &&
      await focus() === 'profileMeTab' && await presence.isHidden() &&
      !/离线演示|dsh-client|刷新诊断|重新绑定/.test(await dialog.innerText()));
    await page.screenshot({path: path.join(screenshots, 'demo-profile-me.png')});
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('#profileName').textContent === '聊天助手');
    check('方向键切换名片，仅对方名片显示在线状态', await focus() === 'profilePeerTab' &&
      await presence.isVisible() && await presence.innerText() === '在线');
    for (const width of [1366, 390, 320]) {
      await page.setViewportSize({width, height: 844});
      const layout = await dialog.evaluate(mask => {
        const card = mask.querySelector('.profile-modal').getBoundingClientRect();
        const tabs = mask.querySelector('.profile-switch').getBoundingClientRect();
        const close = mask.querySelector('#profileClose').getBoundingClientRect();
        const avatar = mask.querySelector('#profileAvatar');
        const image = avatar.getBoundingClientRect();
        const note = mask.querySelector('#profileNote').getBoundingClientRect();
        const name = mask.querySelector('#profileName');
        return avatar.naturalWidth > 0 && card.width === Math.min(420, innerWidth - 32) &&
          card.x >= 16 && card.right <= innerWidth - 16 && tabs.top < close.bottom &&
          tabs.right < close.left && image.width === 62 && image.height === 62 &&
          getComputedStyle(name).fontSize === '20px' && note.top > image.bottom &&
          note.x === image.x && note.right < card.right;
      });
      check(width + ' 宽度名片尺寸和签名位置与家用版一致', layout);
      await page.screenshot({path:path.join(screenshots, `demo-profile-peer-${width}.png`)});
    }
    await page.keyboard.press('Shift+Tab');
    assert.equal(await focus(), 'profileEdit');
    await page.keyboard.press('Tab');
    assert.equal(await focus(), 'profilePeerTab');
    await edit.click();
    assert.equal(await focus(), 'profileNickname');
    await page.locator('#profileNickname').fill('这次不保存');
    assert.equal(await page.locator('#profileNote').isHidden(), true);
    await page.keyboard.press('Escape');
    assert.equal(await focus(), 'profileEdit');
    assert.equal(await page.locator('#profileForm').isHidden(), true);
    assert.equal(await page.locator('#profileNote').isVisible(), true);
    assert.equal(await nickname.textContent(), '聊天助手');
    await page.keyboard.press('Escape');
    check('焦点在名片内循环，退出编辑后再关卡，焦点回到头像', await dialog.isHidden() &&
      await focus() === 'meAvatar');
    await page.setViewportSize({width:1366, height:900});
    await page.route('**/api/peer_profile', route => route.fulfill({json:{ok:false, error:'synthetic failure'}}), {times:1});
    await page.locator('#mainChat .chat-head .av').click();
    await nickname.getByText('名片暂时打开不了', {exact:true}).waitFor();
    assert.equal(await edit.isHidden(), true);
    assert.equal(await page.locator('#profileNote').isHidden(), true);
    await page.locator('#profileRetry').click();
    await nickname.getByText('聊天助手', {exact:true}).waitFor();
    check('读取失败可以重试当前名片，成功后恢复编辑入口', await edit.isVisible() &&
      await page.locator('#profileRetry').isHidden());
    const sample = path.join(root, 'panel/web/assets/default-me.png');
    async function editProfile(name, signature, avatar, capture = false) {
      await edit.click();
      await page.locator('#profileNickname').fill(name);
      await page.locator('#profileBio').fill(signature);
      await page.locator('#profileAvatarFile').setInputFiles(avatar);
      if (capture) {
        await page.setViewportSize({width:390, height:844});
        await page.waitForFunction(() => document.querySelector('#profileAvatarPreview').naturalWidth > 0);
        const fits = await page.locator('#profileBody').evaluate(body => {
          const save = body.querySelector('#profileSave').getBoundingClientRect();
          return body.scrollWidth === body.clientWidth && save.right < innerWidth && save.bottom < innerHeight;
        });
        check('窄屏编辑态完整显示，头像可预览', fits);
        await page.screenshot({path:path.join(screenshots, 'demo-profile-editor-390.png')});
        await page.setViewportSize({width:1366, height:900});
      }
      await page.locator('#profileSave').click();
      await page.locator('#profileAppearanceHint').getByText('已保存。', {exact:true}).waitFor();
      await page.waitForFunction(value => {
        const image = document.querySelector('#profileAvatar');
        return document.querySelector('#profileName').textContent === value &&
          image.src.includes('/appearance/') && image.naturalWidth > 0;
      }, name);
      assert.equal(await page.locator('#profileSignature').textContent(), signature);
    }
    await editProfile('对方本机昵称', '对方本机签名\n签名第二行', sample, true);
    await page.locator('#profileMeTab').click();
    await page.waitForFunction(() => document.querySelector('#profileName').textContent === '我');
    await editProfile('我的本机昵称', '我的本机签名', path.join(root, 'panel/web/assets/default-peer.png'));
    await page.locator('#profileClose').click();
    check('双方头像与昵称改完即同步到列表、栏头和气泡', await page.evaluate(() =>
      document.querySelector('.conv[data-chat="yu"] .nm').textContent === '对方本机昵称' &&
      document.querySelector('.chat-head .hname').firstChild.textContent.trim() === '对方本机昵称' &&
      document.querySelector('#meAvatar img').src.includes('/appearance/me/') &&
      [...document.querySelectorAll('#stream .msg.me .mav')].every(image => image.src.includes('/appearance/me/')) &&
      [...document.querySelectorAll('#stream .msg.her .mav')].every(image => image.src.includes('/appearance/peer/'))));
    await page.locator('#ta').fill('浏览器离线验收');
    await page.locator('#sendBtn').click();
    await page.waitForFunction(() => CHATS.yu.msgs.some(row => row.text === '演示回复：浏览器离线验收'));
    check('通过浏览器接口完成消息来回', await page.evaluate(() => 聊天.总 === 36));
    await page.waitForTimeout(1300);
    const before = await page.evaluate(() => CHATS.yu.msgs.length);
    await page.locator('#openPond').click();
    await page.locator('#pondAsk').getByText('不会重置连接端的对话或记忆。', {exact:false}).waitFor();
    await page.locator('#pondAskNo').click();
    check('取消仅本地分段，不改变记录', await page.evaluate(() => CHATS.yu.msgs.length) === before);
    await page.locator('#openPond').click();
    await page.locator('#pondAskYes').click();
    await page.waitForFunction(() => CHATS.yu.msgs.some(row => row.k === 'pond'));
    await page.waitForTimeout(1500);
    check('归档保留旧消息且不会重放队列',
      await page.evaluate(() => CHATS.yu.msgs.length) === before + 1);
    await page.locator('#ta').fill('归档后的消息');
    await page.locator('#sendBtn').click();
    await page.waitForFunction(() => CHATS.yu.msgs.some(row => row.text === '演示回复：归档后的消息'));
    check('归档后继续收发', await page.evaluate(() => 聊天.总 === 38));
    await page.locator('#tHistory').click();
    await page.locator('#histQ').fill('浏览器离线验收');
    await page.waitForFunction(() => 记录簿.词 === '浏览器离线验收' && !记录簿.取着 && 记录簿.总 === 2);
    check('搜索能找到归档内两侧消息', await page.locator('.hist-row').count() === 2);
    const names = await page.locator('.hist-row .hist-whom b').allTextContents();
    assert.deepEqual(names.sort(), ['对方本机昵称', '我的本机昵称'].sort());
    check('聊天记录显示双方本机资料', await page.locator('.hist-row .hist-av').evaluateAll(images =>
      images.every(image => image.src.includes('/appearance/'))));
    await page.locator('#histClose').click();
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.conv[data-chat="yu"] .nm').textContent === '对方本机昵称');
    await page.locator('#stream .pond-sep').waitFor();
    check('重开面板仍读取到本地分隔线', await page.locator('#stream .pond-sep').count() === 1);
    await page.locator('#meAvatar').click();
    await page.waitForFunction(() => document.querySelector('#profileName').textContent === '我的本机昵称');
    const defaultNames = {me: '我', peer: '聊天助手'};
    for (const role of ['me', 'peer']) {
      if (role === 'peer') {
        await page.locator('#profilePeerTab').click();
        await page.waitForFunction(() => document.querySelector('#profileName').textContent === '对方本机昵称');
      }
      await page.locator('#profileEdit').click();
      await page.locator('#profileReset').click();
      await page.locator('#profileAppearanceHint').getByText('已恢复默认。', {exact:true}).waitFor();
      assert.equal(await page.locator('#profileName').textContent(), defaultNames[role]);
      assert.equal(await page.locator('#profileAvatar').getAttribute('src'), `assets/default-${role}.png`);
    }
    check('刷新后保留，双方独立恢复到各自默认头像', true);
    await page.locator('#profileClose').click();
    await page.screenshot({path: path.join(screenshots, 'demo-desktop.png')});
    await page.locator('.rbtn[data-feature="paint"]').click();
    await page.waitForFunction(() => !画册.取着 && 画册.画.length === 12);
    check('真实画册按页载入合成作品', await page.evaluate(() => 画册.总 === 18));
    await page.waitForFunction(() => [...document.querySelectorAll('.gal-open img')]
      .filter(image => image.getBoundingClientRect().top < innerHeight)
      .every(image => image.naturalWidth > 0));
    await page.screenshot({path: path.join(screenshots, 'demo-gallery.png')});
    await page.locator('.gal-open').first().click();
    await page.waitForFunction(() => document.querySelector('#galViewer').open &&
      document.querySelector('#galViewImg').naturalWidth > 0);
    check('真实媒体服务能读取作品预览', true);
    await page.keyboard.press('Escape');
    await page.locator('.rbtn[data-nav="setting"]').click();
    await page.locator('#mediaSettingsBtn').click();
    await page.waitForFunction(() => 媒体管理状态.设置 != null);
    check('媒体管理可读取独立数据目录', await page.locator('#mediaTempDays').inputValue() === '30');
    await page.locator('#mediaClose').click();
    for (const width of [760, 390]) {
      await page.setViewportSize({width, height: 844});
      await page.screenshot({path: path.join(screenshots, 'demo-gallery-' + width + '.png')});
      check(width + ' 宽度画册无横向溢出', await page.evaluate(() =>
        document.documentElement.scrollWidth <= innerWidth + 1 &&
        document.querySelector('#galScroll').getBoundingClientRect().width > 200));
    }
    await page.setViewportSize({width:1366, height:900});
    const nav = name => page.locator(`[data-feature="${name}"],[data-nav="${name}"]`);
    await nav('chat').click();
    await page.locator('#ta').fill('切页时保留这句草稿');
    const reading = await page.locator('#stream').evaluate(node => {
      node.dispatchEvent(new WheelEvent('wheel'));
      node.scrollTop = 520;
      return node.scrollTop;
    });
    assert.ok(reading > 0);
    await nav('paint').click();
    await page.locator('#galSearch').fill('sample');
    await page.waitForFunction(() => !画册.待搜索 && !画册.取着);
    const galleryTop = await page.locator('#galScroll').evaluate(node => {
      node.scrollTop = 110;
      return node.scrollTop;
    });
    await nav('board').click();
    await page.locator('#journalEmpty').waitFor({state:'visible'});
    const today = await page.locator('#journalDate').inputValue();
    await page.locator('#journalEdit').click();
    const diary = '普通的今天。\n<b>保留为原文</b>\n' + '这是隔离验收的一段日记。\n'.repeat(85);
    await page.locator('#journalText').fill(diary);
    await nav('service').click();
    await page.locator('#panel').waitFor({state:'visible'});
    assert.equal(await nav('service').getAttribute('aria-current'), 'page');
    await nav('sing').click();
    await page.locator('#stagePane').waitFor({state:'visible'});
    await nav('board').click();
    assert.equal(await page.locator('#journalText').inputValue(), diary);
    await page.locator('#journalSave').click();
    await page.locator('#journalStatus').getByText('已保存', {exact:true}).waitFor();
    await page.locator('#journalBody').waitFor({state:'visible'});
    assert.equal(await page.locator('#journalBody').textContent(), diary);
    assert.equal(await page.locator('#journalBody b').count(), 0);
    const diaryTop = await page.locator('#journalPage').evaluate(node => {
      node.scrollTop = 350;
      return node.scrollTop;
    });
    await nav('paint').click();
    assert.equal(await page.locator('#galSearch').inputValue(), 'sample');
    assert.equal(await page.locator('#galScroll').evaluate(node => node.scrollTop), galleryTop);
    await nav('board').click();
    assert.equal(await page.locator('#journalPage').evaluate(node => node.scrollTop), diaryTop);
    await page.screenshot({path:path.join(screenshots, 'demo-diary-desktop.png')});
    await nav('chat').click();
    assert.equal(await page.locator('#ta').inputValue(), '切页时保留这句草稿');
    assert.ok(Math.abs(await page.locator('#stream').evaluate(node => node.scrollTop) - reading) < 2);
    check('五页直接互切，保留两种草稿和三处浏览位置', true);
    await page.reload();
    await nav('board').click();
    await page.locator('#journalBody').waitFor({state:'visible'});
    assert.equal(await page.locator('#journalDate').inputValue(), today);
    assert.equal(await page.locator('#journalBody').textContent(), diary);
    for (const width of [760, 390]) {
      await page.setViewportSize({width, height:844});
      assert.equal(await page.locator('#journalDate').evaluate(node =>
        node.getBoundingClientRect().right <= innerWidth && node.checkVisibility()), true);
      assert.equal(await page.locator('#localDiary').evaluate(node => node.scrollWidth === node.clientWidth), true);
      await page.screenshot({path:path.join(screenshots, 'demo-diary-' + width + '.png')});
    }
    check('日记重开仍在，窄屏能读取日期，没有横向溢出', true);
    const diaryRoot = path.join(temp, 'data', 'diary');
    const dateFromAgent = '2025-02-28';
    fs.writeFileSync(path.join(diaryRoot, dateFromAgent + '.md'), '助手直接写入的日记。\n', 'utf8');
    fs.copyFileSync(sample, path.join(diaryRoot, 'photos', dateFromAgent + '-example.png'));
    await page.getByRole('button', {name:dateFromAgent, exact:true}).waitFor();
    await page.getByRole('button', {name:dateFromAgent, exact:true}).click();
    await page.locator('#journalBody').getByText('助手直接写入的日记。', {exact:true}).waitFor();
    await page.waitForFunction(() => document.querySelector('#journalPhotos img')?.naturalWidth > 0);
    check('助手放入的日期正文和合成配图会自动出现', await page.locator('#journalDate').inputValue() === dateFromAgent);
    await page.locator('#journalEdit').click();
    await page.locator('#journalText').fill('未提交草稿');
    const choosePhoto = page.waitForEvent('filechooser');
    await page.getByRole('button', {name:'加照片', exact:true}).click();
    await (await choosePhoto).setFiles(sample);
    await page.waitForFunction(() => document.querySelectorAll('#journalPhotos button').length === 2);
    assert.equal(await page.locator('#journalText').inputValue(), '未提交草稿');
    await page.locator('#journalPhotoFile').setInputFiles(sample);
    await page.getByText('这张照片已经在日记里了', {exact:true}).waitFor();
    check('从页面加照片保留文字草稿，重复选图只存一份', true);
    fs.writeFileSync(path.join(diaryRoot, dateFromAgent + '.md'), '助手对正文的更新。', 'utf8');
    await page.getByText('正文已在别处更新。重新读取后可检查你的草稿。', {exact:true}).waitFor();
    assert.equal(await page.locator('#journalText').inputValue(), '未提交草稿');
    assert.equal(await page.locator('#journalSave').isDisabled(), true);
    await page.locator('#journalRefresh').click();
    await page.getByText('已读取最新正文，你原来的草稿仍保留。', {exact:true}).waitFor();
    check('外部更新不会抢走编辑中的草稿，也不会被旧草稿覆盖', await page.locator('#journalSave').isEnabled());
    await page.locator('.rbtn[data-nav="chat"]').click();
    await page.locator('.conv[data-chat="yu"]').click();
    await page.screenshot({path: path.join(screenshots, 'demo-mobile.png')});
    check('窄屏输入框完整可见', await page.locator('#ta').evaluate(node => {
      const box = node.getBoundingClientRect();
      return box.x >= 0 && box.right <= innerWidth && box.bottom <= innerHeight;
    }));
    await page.setViewportSize({width:1366, height:900});
    await checkSongs(page, path.join(temp, 'data'));
    check('演示全程没有访问外部服务', external.length === 0);
    check('页面和资源没有运行错误', errors.length === 0 && failed.length === 0);
    console.log(JSON.stringify({passed: checks.length, errors, external, failed, screenshots}, null, 2));
  } finally {
    if (browser) await browser.close();
    if (service && service.exitCode === null) {
      const closed = new Promise(resolve => service.once('close', resolve));
      service.kill();
      await closed;
    }
    assert.equal(path.dirname(temp), path.resolve(os.tmpdir()));
    fs.rmSync(temp, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
