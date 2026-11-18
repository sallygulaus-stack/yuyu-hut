/* 与临时验收服务配对；浏览器只连假桥，不发送聊天，不操作真实画册。 */
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const 项目 = path.resolve(__dirname, '../..');
let chromium;
try {
  ({chromium} = require(process.env.PLAYWRIGHT_CORE || 'playwright-core'));
} catch (e) {
  console.error('缺少 playwright-core：请安装项目测试依赖，或设置 PLAYWRIGHT_CORE 指向该模块。');
  process.exitCode = 2;
}
const Python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || 'python';
const 媒体模式 = process.argv.includes('--媒体库');
let 截图;
const 检查 = [];

function 验(名, 值) {
  assert.ok(值, 名);
  检查.push(名);
  console.log('通过：' + 名);
}

(async () => {
  if (!chromium) return;
  截图 = fs.mkdtempSync(path.join(os.tmpdir(), 'gallery-browser-'));
  let 浏览器, 服务, 地址;
  let 错 = '';
  try {
    服务 = spawn(Python, ['-B', path.join(__dirname, '画册验收服务.py'),
      ...(媒体模式 ? ['--媒体库'] : [])], {cwd: 项目, windowsHide: true});
    服务.stderr.on('data', x => { 错 += String(x); });
    地址 = await new Promise((好, 坏) => {
      const 行 = readline.createInterface({input: 服务.stdout});
      const 表 = setTimeout(() => 坏(new Error('测试服务没有启动：' + 错)), 30000);
      行.on('line', s => {
        try {
          const r = JSON.parse(s);
          if (r.端口) { clearTimeout(表); 好('http://127.0.0.1:' + r.端口); }
        } catch (_) {}
      });
      服务.once('error', 坏);
      服务.once('exit', 码 => { if (码) { clearTimeout(表); 坏(new Error('测试服务退出：' + 错)); } });
    });
    const 调 = async (名, 参数 = []) => (await fetch(地址 + '/__gallery/' + 名, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(参数)
    })).json();
    const 浏览器参数 = {headless: true};
    if (process.env.BROWSER_EXECUTABLE) 浏览器参数.executablePath = process.env.BROWSER_EXECUTABLE;
    else 浏览器参数.channel = process.env.BROWSER_CHANNEL || 'msedge';
    浏览器 = await chromium.launch(浏览器参数);
    const 页 = await 浏览器.newPage({viewport: {width: 1366, height: 900}, deviceScaleFactor: 1});
    const 页面错误 = [], 请求 = [];
    页.on('pageerror', e => 页面错误.push(e.message));
    页.on('request', r => 请求.push(decodeURIComponent(new URL(r.url()).pathname)));
    await 页.addInitScript(媒体模式 => {
      window.__galleryControl = {delayNew: 0, delayAdd: 0, delayMove: 0, failNext: false,
        calls: [], responses: 0, holdNew: false, release: null};
      const 调 = async (名, 参数) => {
        const r = await fetch('/__gallery/' + 名, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(参数)
        });
        return r.json();
      };
      const api = {
        gallery_list: async (...args) => {
          const c = window.__galleryControl;
          c.calls.push(args);
          if (c.failNext) { c.failNext = false; throw new Error('模拟：连接临时中断'); }
          const 慢 = args[2] === '新' ? c.delayNew : 0;
          const r = await 调('gallery_list', args);
          c.responses++;
          if (args[2] === '新' && c.holdNew) await new Promise(ok => { c.release = ok; });
          if (慢) await new Promise(ok => setTimeout(ok, 慢));
          return r;
        },
        gallery_status: (...args) => 调('gallery_status', args),
        gallery_add: async (...args) => {
          const r = await 调('gallery_add', args);
          await new Promise(ok => setTimeout(ok, window.__galleryControl.delayAdd));
          return r;
        },
        gallery_del: async (...args) => {
          const r = await 调('gallery_del', args);
          await new Promise(ok => setTimeout(ok, window.__galleryControl.delayMove));
          return r;
        },
        gallery_restore: (...args) => 调('gallery_restore', args),
        gallery_export: (...args) => 调('gallery_export', args),
        gallery_collect: (...args) => 调('gallery_collect', args),
        chat_history: async () => ({ok: true, 消息: [], 还有: false}),
        chat_poll: async () => ({ok: true, 来信: []}),
        chat_ready: async () => ({通: true, 连接: true}),
        chat_send: async () => { throw new Error('验收不允许发送聊天'); },
        chat_send_image: async () => { throw new Error('验收不允许发送聊天'); },
        state: async () => ({services: [], ip: '', state: 'online'}),
        cost: async () => ({ok: true, cost: 0}),
        get_skin: async () => ({ok: true, skin: 'sea'}),
        fake_info: async () => ({ok: false}),
        sticker_list: async () => ({ok: true, 表情包: [], 食物: []}),
        diary_list: async () => ({ok: true, 条: []}),
        song_list: async () => ({ok: true, 歌: []})
      };
      window.pywebview = {api: new Proxy(api, {
        get: (目标, 键) => 键 === 'gallery_export' && !媒体模式 ? undefined :
          (目标[键] || (async () => ({ok: true})))
      })};
    }, 媒体模式);
    await 页.goto(地址 + '/index.html', {waitUntil: 'domcontentloaded'});
    await 页.waitForTimeout(850);
    await 页.locator('.rbtn[data-feature="paint"]').click();
    const 等画 = async () => 页.waitForFunction(() => !画册.取着 && !画册.待搜索 && 画册.画.length > 0);
    await 等画();
    const 初 = await 页.evaluate(() => ({总: 画册.总, 取: 画册.画.length, 列: 画册列数()}));
    验('首批按页载入', 初.取 === 12 && 初.总 >= 40);
    验('列数按画册实际宽度计算', 初.列 >= 3);
    await 页.waitForTimeout(500);
    验('浏览首批没有请求原图', !请求.some(p => p.startsWith('/assets/画册/')) &&
      !await 页.evaluate(() => performance.getEntriesByType('resource').some(r =>
        new URL(r.name).pathname.startsWith('/media/') && !new URL(r.name).searchParams.has('s'))));
    验('可见作品的小图已显示', await 页.locator('.gal-open img').evaluateAll(ns => ns.filter(n => n.naturalWidth > 0).length >= 4));
    fs.mkdirSync(截图, {recursive: true});
    await 页.screenshot({path: path.join(截图, '画册-桌面.png')});
    if (process.argv.includes('--排队')) {
      await 页.evaluate(() => { window.__galleryControl.delayMove = 700; });
      await 页.locator('.gal-item').first().locator('.gal-card-foot button').click();
      await 页.locator('#galMonth').selectOption('2026-10');
      await 页.locator('#galSort').selectOption('旧');
      await 页.locator('#galMonth').selectOption('2026-09');
      await 页.waitForFunction(() => !画册.操作.size && !画册.取着 && 画册.画.length &&
        画册.画.every(x => 月键(x.时) === '2026-09'));
      验('收纳期间连续切换筛选会执行最后一次选择', await 页.evaluate(() =>
        画册.月份 === '2026-09' && 画册.排序 === '旧' && 画册.总 === 1 && !画册.待重取));
      const 原件 = await 调('核对原件');
      验('补测没有改动测试夹具源图', 原件.ok);
      验('补测页面没有脚本异常', !页面错误.length);
      await 调('结束验收');
      return;
    }

    const 固定前 = await 页.locator('.gal-head').boundingBox();
    const 原位置 = await 页.evaluate(() => { $('#galScroll').scrollTop = 320; return $('#galScroll').scrollTop; });
    await 页.evaluate(() => 取一批(false));
    验('追加作品不改变滚动位置', Math.abs(await 页.evaluate(() => $('#galScroll').scrollTop) - 原位置) < 2);
    验('工具栏固定在原位', Math.abs((await 页.locator('.gal-head').boundingBox()).y - 固定前.y) < 1);
    for (let i = 0; i < 12; i++) {
      const 完 = await 页.evaluate(() => 画册.完了);
      if (完) break;
      const 前 = await 页.evaluate(() => { $('#galScroll').scrollTop = $('#galScroll').scrollHeight; return 画册.画.length; });
      await 页.waitForFunction(前 => 画册.完了 || 画册.画.length > 前, 前);
    }
    验('滚动续取能展示全册且没有重复', await 页.evaluate(() =>
      画册.完了 && 画册.画.length === 画册.总 && new Set(画册.画.map(x => x.名)).size === 画册.总));
    await 页.locator('.gal-open').first().click();
    await 页.waitForFunction(() => $('#galViewer').open && $('#galViewImg').naturalWidth > 0 && $('#galViewStatus').hidden);
    验('预览按需载入原图', 请求.some(p => p.startsWith(媒体模式 ? '/media/' : '/assets/画册/')));
    const 首名 = await 页.locator('#galViewName').textContent();
    await 页.locator('#galZoomIn').click();
    验('预览可以放大', await 页.evaluate(() => 画册.倍 > 1));
    const 图框 = await 页.locator('#galViewImg').boundingBox();
    await 页.mouse.move(图框.x + 图框.width / 2, 图框.y + 图框.height / 2);
    await 页.mouse.down();
    await 页.mouse.move(图框.x + 图框.width / 2, 图框.y + 图框.height / 2 + 80, {steps: 4});
    await 页.mouse.up();
    验('放大后能够拖动查看细节', await 页.evaluate(() => Math.abs(画册.纵) > 0));
    await 页.locator('#galZoomFit').click();
    验('预览可以恢复适应窗口', await 页.locator('#galZoomLevel').textContent() === '适应');
    await 页.keyboard.press('ArrowRight');
    await 页.waitForFunction(首名 => $('#galViewName').textContent !== 首名, 首名);
    验('键盘能够切到下一张', await 页.locator('#galViewName').textContent() !== 首名);
    await 页.screenshot({path: path.join(截图, '画册-原图预览.png')});
    await 页.keyboard.press('Escape');
    验('退出预览回到作品按钮', await 页.evaluate(() => !$('#galViewer').open && document.activeElement.classList.contains('gal-open')));

    await 页.evaluate(() => { $('#galScroll').scrollTop = 550; });
    const 返回前 = await 页.evaluate(() => ({位: $('#galScroll').scrollTop, 数: 画册.画.length}));
    await 页.evaluate(() => pickChat('yu'));
    await 页.evaluate(() => 切功能('paint'));
    await 页.waitForTimeout(250);
    验('离开再回来保留浏览位置和已加载作品', await 页.evaluate(前 =>
      Math.abs($('#galScroll').scrollTop - 前.位) < 2 && 画册.画.length === 前.数, 返回前));
    await 页.locator('#galMainTab').click();
    验('重复点击当前标签不会刷新作品', await 页.evaluate(前 =>
      Math.abs($('#galScroll').scrollTop - 前.位) < 2 && 画册.画.length === 前.数, 返回前));

    await 页.evaluate(() => { window.__galleryControl.delayNew = 800; });
    await 页.locator('#galSort').selectOption('新');
    await 页.locator('#galSort').selectOption('旧');
    await 等画();
    const 旧首 = await 页.evaluate(() => 画册.画[0].名);
    await 页.waitForTimeout(950);
    验('慢请求不会覆盖新的排序', await 页.evaluate(首 => 画册.排序 === '旧' && 画册.画[0].名 === 首 && !画册.取着, 旧首));
    await 页.locator('#galSort').selectOption('新');
    await 页.locator('#galSearch').fill('特殊文件名');
    await 页.waitForFunction(() => !画册.取着 && 画册.词 === '特殊文件名' && 画册.画.length === 1);
    await 页.waitForTimeout(900);
    验('加载中搜索不会混入旧条件作品', await 页.evaluate(() => 画册.画.length === 1 && 画册.总 === 1));
    验('百分号井号文件名的小图正常显示', await 页.locator('.gal-open img').evaluate(im => im.complete && im.naturalWidth > 0 && !im.hidden));
    await 页.locator('.gal-open').click();
    await 页.waitForFunction(() => $('#galViewStatus').hidden && $('#galViewImg').naturalWidth > 0);
    验('特殊文件名的原图能打开', await 页.locator('#galViewImg').evaluate((im, media) =>
      media ? im.currentSrc.includes('/media/m_') : im.currentSrc.includes('%23100%25'), 媒体模式));
    await 页.keyboard.press('Escape');
    await 页.evaluate(() => { window.__galleryControl.delayNew = 0; });
    await 页.locator('#galSearch').fill('不存在的作品名称');
    await 页.waitForFunction(() => !画册.取着 && 画册.总 === 0);
    验('无搜索结果时能清除筛选', await 页.locator('.gal-empty button').isVisible());
    await 页.locator('.gal-empty button').click();
    await 等画();
    await 页.locator('#galMonth').selectOption('2026-10');
    await 等画();
    验('月份筛选正确', await 页.evaluate(() => 画册.总 === 1 && 画册.画.every(x => 月键(x.时) === '2026-10')));
    const 调用前 = await 页.evaluate(() => window.__galleryControl.calls.length);
    await 页.locator('#galGroup').uncheck();
    验('分组开关独立于排序且不重新取数据', await 页.locator('.gal-month').count() === 0 &&
      await 页.evaluate(前 => window.__galleryControl.calls.length === 前, 调用前));
    await 页.locator('#galGroup').check();
    await 页.locator('#galMonth').selectOption('');
    await 等画();

    await 页.evaluate(() => { window.__galleryControl.failNext = true; });
    await 页.locator('#galRefresh').click();
    await 页.waitForSelector('#galStatus.error');
    const 失败调用 = await 页.evaluate(() => window.__galleryControl.calls.length);
    await 页.waitForTimeout(900);
    验('失败提示持续显示且不会自动反复请求', await 页.locator('#galStatus.error button').isVisible() &&
      await 页.evaluate(前 => window.__galleryControl.calls.length === 前 && $('#galSkel').hidden, 失败调用));
    await 页.locator('#galStatus.error button').click();
    await 等画();
    验('失败后重试可以恢复', !await 页.locator('#galStatus').evaluate(n => n.classList.contains('error')));
    const 小图路 = await 页.evaluate(() => new URL(小图址(画册.画[0]), location.href).pathname);
    await 页.route(url => url.pathname === 小图路, r => r.fulfill({status: 404, body: 'missing thumbnail'}), {times: 1});
    await 页.locator('#galRefresh').click();
    await 等画();
    await 页.waitForFunction(media => {
      const im = $('.gal-open img');
      return im.naturalWidth > 0 && (media
        ? im.currentSrc.includes('/media/m_') && !new URL(im.currentSrc).searchParams.has('s')
        : im.currentSrc.includes('/%E7%94%BB%E5%86%8C/'));
    }, 媒体模式);
    验('小图失效时自动退回原图', await 页.locator('.gal-open img').first().evaluate(n => !n.hidden));
    const 大图路 = await 页.evaluate(() => new URL(原图址(画册.画[0]), location.href).pathname);
    await 页.evaluate(() => { 画册.画[0].版本 += '-failure-test'; });
    await 页.route(url => url.pathname === 大图路, r => r.fulfill({status: 404, body: 'missing original'}), {times: 1});
    await 页.locator('.gal-open').first().click();
    await 页.waitForSelector('#galViewStatus button');
    验('原图失效会提供重新加载入口', await 页.locator('#galViewStatus button').isVisible());
    await 页.locator('#galViewStatus button').click();
    await 页.waitForFunction(() => $('#galViewStatus').hidden);
    验('原图重试能恢复显示', await 页.locator('#galViewImg').evaluate(n => !n.hidden && n.naturalWidth > 0));
    for (let i = 0; i < 10; i++) await 页.keyboard.press('Tab');
    验('键盘焦点留在预览窗口内', await 页.evaluate(() => $('#galViewer').contains(document.activeElement)));
    await 页.keyboard.press('Escape');

    const 移前 = await 页.evaluate(() => ({总: 画册.总, 名: 画册.画[0].名}));
    await 页.locator('.gal-item').first().locator('.gal-card-foot button').click();
    await 页.waitForFunction(名 => !画册.画.some(x => x.名 === 名), 移前.名);
    验('移入收纳箱后计数同步', await 页.evaluate(前 => 画册.总 === 前.总 - 1, 移前));
    await 页.locator('#galUndoBtn').click();
    await 页.waitForFunction(() => $('#galUndo').hidden);
    验('撤销移除会立即恢复作品', await 页.evaluate(前 => 画册.画.some(x => x.名 === 前.名) && 画册.总 === 前.总, 移前));
    await 页.locator('.gal-item').first().locator('.gal-card-foot button').click();
    await 页.waitForFunction(() => !$('#galUndo').hidden);
    await 页.locator('#galTrashTab').click();
    await 等画();
    验('收纳箱能找到移走的原图', await 页.evaluate(() => 画册.总 === 1 && 画册.画[0].收纳));
    await 页.locator('.gal-open').first().click();
    await 页.waitForFunction(() => $('#galViewStatus').hidden && $('#galViewImg').naturalWidth > 0);
    验('收纳箱原图可以预览', await 页.locator('#galViewImg').evaluate((im, media) => media
      ? im.currentSrc.includes('/media/m_')
      : im.currentSrc.includes('/画册收纳/') || im.currentSrc.includes(encodeURIComponent('画册收纳')), 媒体模式));
    if (媒体模式) {
      await 页.locator('#galSave').click();
      await 页.waitForFunction(() => !$('#galSave').disabled);
      const 导出 = await 调('导出结果');
      验('媒体库收纳作品通过实际导出接口保存并解码', 导出.ok && 导出.文件.length === 1 &&
        导出.文件.every(x => x.宽 > 0 && x.高 > 0 &&
          ({WEBP: '.webp', PNG: '.png', JPEG: '.jpg', GIF: '.gif'}[x.格式] === path.extname(x.名))));
    }
    await 页.keyboard.press('Escape');
    await 页.locator('.gal-item').first().locator('.gal-card-foot button').click();
    await 页.waitForFunction(() => 画册.总 === 0 && !画册.取着);
    验('恢复后收纳箱显示空态', await 页.locator('.gal-empty').isVisible());
    await 页.locator('#galMainTab').click();
    await 等画();
    验('恢复后画册总数正确', await 页.evaluate(前 => 画册.总 === 前.总, 移前));
    await 页.evaluate(() => { window.__galleryControl.delayMove = 500; });
    const 双移 = await 页.evaluate(() => [$('.gal-item').dataset.n, $$('.gal-item')[1].dataset.n]);
    const 双移前 = await 页.evaluate(() => 画册.总);
    await 页.locator('.gal-item').nth(0).locator('.gal-card-foot button').click();
    await 页.locator('.gal-item').nth(1).locator('.gal-card-foot button').click();
    await 页.waitForFunction(名们 => 名们.every(名 => !画册.画.some(x => x.名 === 名)), 双移);
    验('同时收纳两张都能从页面移走', await 页.evaluate(前 => 画册.总 === 前 - 2, 双移前));
    await 页.evaluate(() => { window.__galleryControl.delayMove = 0; });
    const 收纳 = await 调('gallery_list', [0, 60, '新', '', '', null, true]);
    for (const x of 收纳.画) await 调('gallery_restore', [x.名]);
    await 页.locator('#galRefresh').click();
    await 等画();

    const 样本 = await 调('上传样本');
    const 上传前 = await 页.evaluate(() => 画册.总);
    const buffer = Buffer.from(样本.b64, 'base64');
    const 上传框 = 页.locator('input[type="file"][multiple][accept^="image/"]');
    await 页.evaluate(() => { window.__galleryControl.delayAdd = 700; });
    await 上传框.setInputFiles([
      {name: '验收 #50%.png', mimeType: 'image/png', buffer},
      {name: '重复的一张.png', mimeType: 'image/png', buffer}
    ]);
    await 页.locator('.gal-open').first().click();
    const 上传时看的 = await 页.locator('#galViewName').textContent();
    await 页.waitForFunction(() => !$('#galAdd').disabled);
    验('后台加图不会打断原图预览', await 页.evaluate(名 => $('#galViewer').open && $('#galViewName').textContent === 名, 上传时看的));
    await 页.keyboard.press('Escape');
    await 页.locator('#galUpdate').click();
    await 等画();
    验('批量上传会跳过重复图', await 页.evaluate(前 => 画册.总 === 前 + 1, 上传前));
    let 上传项;
    if (媒体模式) {
      await 页.locator('#galSearch').fill('验收 #50%');
      await 页.waitForFunction(() => !画册.取着 && !画册.待搜索 &&
        画册.词 === '验收 #50%' && 画册.画.length === 1);
      上传项 = await 页.evaluate(() => 画册.画[0]);
      验('媒体库上传返回稳定 ID 和归档缩略图', 上传项.名.startsWith('m_') &&
        上传项.小图.startsWith('/media/') && 上传项.原图.startsWith('/media/'));
      await 页.evaluate(名 => 开画册预览(名), 上传项.名);
      await 页.waitForFunction(() => $('#galViewStatus').hidden && $('#galViewImg').naturalWidth > 0);
      验('媒体库上传的透明图可预览', await 页.locator('#galViewImg').evaluate(im =>
        im.naturalWidth === 720 && im.naturalHeight === 960));
      await 页.keyboard.press('Escape');
      const 版本前 = await 页.evaluate(() => 画册.版本);
      await 上传框.setInputFiles([
        {name: '重复不改版本.png', mimeType: 'image/png', buffer}
      ]);
      await 页.waitForFunction(() => !$('#galAdd').disabled);
      for (let i = 0; i < 3; i++) await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
      验('重复上传后连续核对不产生假更新', await 页.evaluate(版 =>
        画册.版本 === 版 && !画册.有更新 && $('#galNotice').hidden, 版本前));
      await 页.locator('#galClear').click();
      await 页.waitForFunction(() => !画册.取着 && !画册.待搜索 && !画册.词 && 画册.画.length > 0);
    } else {
      验('上传保留原始字节', Buffer.compare(Buffer.from(await (await fetch(地址 + '/assets/画册/' + encodeURIComponent('验收 #50%.png'))).arrayBuffer()), buffer) === 0);
    }
    const 外部前 = await 页.evaluate(() => ({数: 画册.画.length, 位: $('#galScroll').scrollTop}));
    const 外部 = await 调('外部改动');
    await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
    验('外部新增只提示更新并保留当前浏览', await 页.locator('#galNotice').isVisible() &&
      await 页.evaluate(前 => 画册.画.length === 前.数 && $('#galScroll').scrollTop === 前.位, 外部前));
    if (媒体模式) {
      const 已确认 = await 页.evaluate(() => 画册.版本);
      for (let i = 0; i < 3; i++) await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
      验('同一未查看版本多次核对只保留一个提示', await 页.locator('#galNotice').count() === 1 &&
        await 页.evaluate(版 => 画册.有更新 && 画册.版本 === 版, 已确认));
    }
    await 页.evaluate(() => { window.__galleryControl.failNext = true; });
    await 页.locator('#galUpdate').click();
    await 页.waitForSelector('#galStatus.error');
    验('刷新失败不会吞掉尚未查看的更新', await 页.locator('#galNotice').isVisible());
    await 页.locator('#galStatus.error button').click();
    await 等画();
    验('刷新后纳入外部新增作品', await 页.evaluate(名 => 画册.画.some(x => x.名 === 名), 外部.名));
    await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
    await 页.waitForTimeout(100);
    验('同一版本重复轮询不再重复提示', await 页.locator('#galNotice').isHidden());
    let 刷新中新作;
    if (媒体模式) {
      for (let i = 0; i < 3; i++) await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
      验('成功查看更新后连续核对仍无提示', await 页.locator('#galNotice').isHidden());
      const 响应前 = await 页.evaluate(() => window.__galleryControl.responses);
      await 页.evaluate(() => { window.__galleryControl.holdNew = true; });
      await 页.locator('#galRefresh').click();
      await 页.waitForFunction(前 => window.__galleryControl.responses > 前 &&
        window.__galleryControl.release && 画册.取着, 响应前);
      刷新中新作 = await 调('外部改动');
      await 页.evaluate(() => {
        window.__galleryControl.holdNew = false;
        window.__galleryControl.release();
        window.__galleryControl.release = null;
      });
      await 等画();
      await 页.evaluate(() => { 画册.核对时 = 0; return 核对画册(); });
      验('刷新响应在途时到达的新作品不会被确认基线吞掉', await 页.locator('#galNotice').isVisible());
      await 页.locator('#galUpdate').click();
      await 等画();
      验('再次查看后纳入刷新期间的新作品', await 页.evaluate(名 =>
        画册.画.some(x => x.名 === 名) && !画册.有更新, 刷新中新作.名));
    }

    await 页.evaluate(() => 开画册预览(画册.画[画册.画.length - 1].名));
    const 翻前 = await 页.evaluate(() => 画册.画.length);
    await 页.locator('#galNext').click();
    await 页.waitForFunction(前 => 画册.画.length > 前, 翻前);
    验('预览翻到已加载末尾会继续取下一批', await 页.evaluate(前 =>
      画册.画.findIndex(x => x.名 === 画册.预览) === 前, 翻前));
    await 页.keyboard.press('Escape');
    await 调('gallery_del', [媒体模式 ? 上传项.名 : '验收 #50%.png']);
    await 调('gallery_del', [外部.名]);
    if (刷新中新作) await 调('gallery_del', [刷新中新作.名]);
    await 页.locator('#galRefresh').click();
    await 等画();
    await 页.evaluate(() => $('#toast').classList.remove('on'));
    await 页.evaluate(() => { 画册.撤销 = null; $('#galUndo').hidden = true; });

    for (const [width, height] of [[1000, 760], [760, 800], [520, 844], [390, 844]]) {
      await 页.setViewportSize({width, height});
      await 页.waitForTimeout(350);
      await 页.evaluate(() => { $('#galScroll').scrollTop = 0; });
      const 几何 = await 页.evaluate(() => {
        const g = $('#gallery').getBoundingClientRect();
        const ns = [...document.querySelectorAll('.gal-head button,.gal-head select,.gal-search')];
        return {越界: ns.filter(n => {
          const r = n.getBoundingClientRect(); return r.width > 0 && (r.left < g.left - 1 || r.right > g.right + 1);
        }).map(n => n.id || n.className), 横滚: $('#galScroll').scrollWidth > $('#galScroll').clientWidth + 1};
      });
      验(width + ' 宽度工具栏不越界、不产生横向滚动', !几何.越界.length && !几何.横滚);
      await 页.screenshot({path: path.join(截图, '画册-' + width + '.png')});
    }
    await 页.locator('.gal-open').first().click();
    await 页.waitForFunction(() => $('#galViewStatus').hidden && $('#galViewImg').naturalWidth > 0);
    await 页.screenshot({path: path.join(截图, '画册-窄屏预览.png')});
    验('窄屏预览关闭按钮可见', await 页.locator('#galViewClose').isVisible());
    await 页.keyboard.press('Escape');
    await 页.setViewportSize({width: 1000, height: 760});
    await 页.evaluate(() => 套皮('sakura'));
    await 页.waitForTimeout(180);
    await 页.screenshot({path: path.join(截图, '画册-樱贝.png')});
    验('樱贝主题仍有足够的作品浏览区域', await 页.locator('#galScroll').evaluate(n => n.clientHeight > 300));
    const 原件 = await 调('核对原件');
    验('测试夹具源图的内容、数量、修改时间没有改变', 原件.ok);
    验('真实媒体目录、数据库和文件指纹未改变', 原件.真实媒体.unchanged &&
      原件.隔离 && 原件.媒体库 === 媒体模式);
    验('页面没有脚本异常', 页面错误.length === 0 || (console.error(页面错误), false));
    console.log(JSON.stringify({通过: 检查.length, 分支: 媒体模式 ? '媒体库' : '旧目录',
      测试源图数: 原件.原图数, 真实媒体: 原件.真实媒体, 页面错误,
      临时截图数: fs.readdirSync(截图).length}, null, 2));
    await 调('结束验收');
  } finally {
    if (浏览器) await 浏览器.close();
    if (服务 && 服务.exitCode === null) {
      if (地址) {
        try {
          await fetch(地址 + '/__gallery/结束验收', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: '[]'
          });
        } catch (_) {}
      }
      if (服务.exitCode === null) await new Promise(ok => {
        const 表 = setTimeout(() => { 服务.kill(); ok(); }, 3000);
        服务.once('exit', () => { clearTimeout(表); ok(); });
      });
    }
    if (错.trim()) console.error(错.trim());
    assert.equal(path.dirname(截图), path.resolve(os.tmpdir()));
    fs.rmSync(截图, {recursive: true, force: true, maxRetries: 8, retryDelay: 100});
    if (服务 && 服务.exitCode !== 0) throw new Error('验收服务未正常结束：' + 服务.exitCode);
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
