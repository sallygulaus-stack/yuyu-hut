/* 画册只依赖面板现有的 $、toast、读原图；绑定等主脚本完成后再执行。 */
'use strict';

const 画册 = {
  排序: '新', 分组: true, 词: '', 月份: '', 收纳: false, 批: 12,
  总: 0, 全册总: 0, 已取: 0, 取着: false, 完了: false, 进过: false,
  请求: 0, 任务: null, 游标: null, 版本: '', 错误: '', 待搜索: false,
  画: [], 段: {}, 月: {}, 最早: 0, 最新: 0, 操作: new Set(), 待重取: false, 撤销: null,
  预览: null, 倍: 1, 横: 0, 纵: 0, 翻着: false, 核对时: 0, 核对中: false, 有更新: false
};

function 月日(秒) {
  return new Date((秒 || 0) * 1000).toLocaleDateString('zh-CN', {month: 'numeric', day: 'numeric'});
}

function 月键(秒) {
  const d = new Date((秒 || 0) * 1000);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}

function 画册图标(名) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg'), use = document.createElementNS(ns, 'use');
  svg.classList.add('gal-ico');
  svg.setAttribute('aria-hidden', 'true');
  use.setAttribute('href', 'assets/画册图标.svg#' + 名);
  svg.appendChild(use);
  return svg;
}

function 画册按钮(文, 动作, 类 = 'gal-btn') {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 类; b.textContent = 文;
  b.addEventListener('click', 动作);
  return b;
}

function 小图址(x) {
  if (!x.小图) return '';
  const base = String(x.小图).startsWith('/')
    ? String(x.小图)
    : 'assets/画册缓存/' + encodeURIComponent(x.小图);
  return base + (base.includes('?') ? '&' : '?') +
    'v=' + encodeURIComponent(x.版本 || '');
}

function 原图址(x) {
  if (x.原图) return x.原图 + (x.原图.includes('?') ? '&' : '?') +
    'v=' + encodeURIComponent(x.版本 || '');
  return 'assets/' + (x.收纳 ? '画册收纳/' : '画册/') +
    encodeURIComponent(x.名) + '?v=' + encodeURIComponent(x.版本 || '');
}

function 画册列数() {
  const w = $('#galBody').clientWidth;
  return w >= 1100 ? 4 : (w >= 760 ? 3 : (w >= 360 ? 2 : 1));
}

function 画册条件() {
  return JSON.stringify([画册.词, 画册.月份, 画册.排序, 画册.收纳]);
}

function 画一张(x) {
  const d = document.createElement('article');
  d.className = 'gal-item'; d.dataset.n = x.名;
  const 开 = document.createElement('button');
  开.type = 'button'; 开.className = 'gal-open';
  开.setAttribute('aria-label', '预览 ' + x.原名);
  开.style.aspectRatio = (x.宽 || 4) + ' / ' + (x.高 || 3);
  const im = document.createElement('img');
  im.alt = x.原名; im.loading = 'lazy'; im.decoding = 'async';
  const 小 = 小图址(x);
  let 退过 = !小;
  im.addEventListener('error', () => {
    if (!退过) { 退过 = true; im.src = 原图址(x); return; }
    im.hidden = true;
    if (开.querySelector('.gal-broken')) return;
    const 错 = document.createElement('span');
    错.className = 'gal-broken';
    错.append(画册图标('image-off'), document.createTextNode('图片暂时打不开'));
    开.appendChild(错);
  });
  im.src = 小 || 原图址(x);
  开.appendChild(im);
  开.addEventListener('click', () => 开画册预览(x.名));
  const 底 = document.createElement('div');
  底.className = 'gal-card-foot';
  const 时 = document.createElement('time');
  时.dateTime = new Date(x.时 * 1000).toISOString();
  时.textContent = 月日(x.时);
  时.title = new Date(x.时 * 1000).toLocaleString('zh-CN');
  const 尺 = document.createElement('span');
  尺.className = 'gal-dim';
  尺.textContent = x.宽 && x.高 ? x.宽 + ' × ' + x.高 : '尺寸未知';
  const 挪 = 画册按钮('', () => 移动作品(x, 挪), 'gal-icon');
  const 文 = x.收纳 ? '恢复到画册' : '移入收纳箱';
  挪.title = 文; 挪.setAttribute('aria-label', 文 + '：' + x.原名);
  挪.disabled = 画册.操作.has(String(x.收纳) + x.名);
  挪.appendChild(画册图标(x.收纳 ? 'archive-restore' : 'archive'));
  底.append(时, 尺, 挪);
  d.append(开, 底);
  return d;
}

function 取段(x) {
  const 键 = 画册.分组 ? 月键(x.时) : '_';
  if (画册.段[键]) return 画册.段[键];
  const 体 = $('#galBody');
  let 头 = null;
  if (画册.分组) {
    头 = document.createElement('div'); 头.className = 'gal-month';
    const 名 = document.createElement('span'), 数 = document.createElement('i');
    const [年, 月] = 键.split('-');
    名.textContent = 年 + ' 年 ' + Number(月) + ' 月';
    数.textContent = (画册.月[键] || 0) + ' 张';
    头.append(名, 数); 体.appendChild(头);
  }
  const 盒 = document.createElement('div'); 盒.className = 'gal-cols';
  const 列 = Array.from({length: 画册列数()}, () => {
    const c = document.createElement('div'); c.className = 'gal-col'; 盒.appendChild(c); return c;
  });
  体.appendChild(盒);
  const 段 = {头, 盒, 列, 高: 列.map(() => 0)};
  画册.段[键] = 段;
  return 段;
}

function 投一张(x) {
  const 段 = 取段(x), i = 段.高.indexOf(Math.min(...段.高));
  const 宽 = (Math.max(1, $('#galBody').clientWidth) - (段.列.length - 1) * 16) / 段.列.length;
  段.高[i] += 宽 * (x.高 || 3) / (x.宽 || 4) + 60;
  段.列[i].appendChild(画一张(x));
}

function 画册锚点() {
  const 顶 = $('#galScroll').getBoundingClientRect().top;
  const 可见 = [...$('#galBody').querySelectorAll('.gal-item')].map(n => ({n, r: n.getBoundingClientRect()}))
    .filter(x => x.r.bottom > 顶 + 1).sort((a, b) => a.r.top - b.r.top || a.r.left - b.r.left);
  return 可见.length ? {名: 可见[0].n.dataset.n, 偏: 可见[0].r.top - 顶} : null;
}

function 重排画册() {
  if (!$('#galBody').clientWidth) return;
  const 滚 = $('#galScroll'), 位 = 滚.scrollTop, 锚 = 画册锚点();
  const 焦 = document.activeElement.closest('.gal-item');
  const 焦名 = 焦 && 焦.dataset.n;
  $('#galBody').replaceChildren(); 画册.段 = {};
  画册.画.forEach(投一张);
  const 找 = 名 => [...$('#galBody').querySelectorAll('.gal-item')].find(n => n.dataset.n === 名);
  const 新锚 = 锚 && 找(锚.名);
  滚.scrollTop = 位;
  if (新锚) 滚.scrollTop += 新锚.getBoundingClientRect().top - 滚.getBoundingClientRect().top - 锚.偏;
  if (焦名 && 找(焦名)) 找(焦名).querySelector('.gal-open').focus({preventScroll: true});
}

function 刷画册计数() {
  $('#galCount').textContent = 画册.总;
  $('#galFrom').textContent = 画册.总 ? new Date(画册.最早 * 1000).getFullYear() + ' 年起' : '';
  $('#galClear').hidden = !$('#galSearch').value;
  const 月 = $('#galMonth');
  月.replaceChildren(new Option('全部月份', ''));
  Object.keys(画册.月).sort().reverse().forEach(k => 月.add(new Option(k.replace('-', ' 年 ') + ' 月 · ' + 画册.月[k], k)));
  if (画册.月份 && ![...月.options].some(o => o.value === 画册.月份)) 月.add(new Option(画册.月份 + ' · 0', 画册.月份));
  月.value = 画册.月份;
}

function 画册空态() {
  if (画册.画.length || 画册.取着 || 画册.错误) return;
  const 体 = $('#galBody');
  体.replaceChildren();
  const d = document.createElement('div'), b = document.createElement('b'), 文 = document.createElement('span');
  d.className = 'gal-empty';
  const 筛着 = !!(画册.词 || 画册.月份);
  b.textContent = 筛着 ? '没有找到作品' : (画册.收纳 ? '收纳箱是空的' : '还没有作品');
  文.textContent = 筛着 ? '换个文件名，或清除筛选再看看。' :
    (画册.收纳 ? '从画册移走的作品会留在这里，随时可以恢复。' : '她的新画会收在这里，也可以添加你存下的作品。');
  d.append(画册图标(画册.收纳 ? 'archive' : 'images'), b, 文);
  if (筛着) d.appendChild(画册按钮('清除筛选', () => {
    画册.词 = ''; 画册.月份 = ''; $('#galSearch').value = ''; 刷新画册();
  }));
  else if (!画册.收纳) d.appendChild(画册按钮('添加作品', () => $('#galAdd').click()));
  体.appendChild(d);
}

function 刷画册底() {
  const 状 = $('#galStatus'), 底 = $('#galFoot');
  状.replaceChildren(); 状.classList.toggle('error', !!画册.错误);
  $('#galSkel').hidden = !(画册.取着 && !画册.画.length);
  $('#galBody').setAttribute('aria-busy', String(画册.取着));
  $('#galRefresh').setAttribute('aria-busy', String(画册.取着));
  if (画册.错误) {
    状.append(document.createTextNode(画册.错误), 画册按钮('重试', () => 取一批(!画册.画.length)));
  } else if (画册.操作.size) {
    状.textContent = '正在整理作品…';
  } else if (画册.取着) {
    状.textContent = '正在载入作品…';
  } else if (画册.画.length && !画册.完了) {
    状.appendChild(画册按钮('加载更多', () => 取一批(false)));
  } else if (画册.画.length) 状.textContent = 画册.有更新 ? '当前浏览已结束，刷新可查看最新画册' : '已经展示全部作品';
  底.hidden = !画册.画.length;
  底.textContent = '已显示 ' + 画册.画.length + (画册.有更新 ? ' 张' : ' / ' + 画册.总 + ' 张');
  $('#galUndoBtn').disabled = 画册.操作.size > 0;
  画册空态();
}

function 提示画册更新() {
  画册.有更新 = true;
  $('#galNotice').hidden = false;
  刷画册底();
}

async function 画册限时(任务) {
  let 表;
  try {
    return await Promise.race([任务, new Promise((_, 坏) => {
      表 = setTimeout(() => 坏(new Error('等候作品超时了，可以重试或刷新')), 20000);
    })]);
  } finally { clearTimeout(表); }
}

function 取一批(重置 = false) {
  if (画册.操作.size) {
    if (重置) { 画册.请求++; 画册.待重取 = true; }
    return Promise.resolve();
  }
  if (!重置 && (画册.取着 || 画册.完了 || 画册.待搜索)) return 画册.任务 || Promise.resolve();
  if (重置) {
    画册.请求++;
    画册.画 = []; 画册.段 = {}; 画册.已取 = 0; 画册.游标 = null;
    画册.完了 = false; 画册.待搜索 = false;
    if ($('#galViewer').open) $('#galViewer').close();
    $('#galBody').replaceChildren(); $('#galScroll').scrollTop = 0;
  }
  const 代 = 画册.请求, 续 = 画册.游标, 本次重置 = 重置;
  const 参数 = [画册.已取, 画册.批, 画册.排序, 画册.词, 画册.月份, 续, 画册.收纳, 画册.版本];
  画册.进过 = true; 画册.取着 = true; 画册.错误 = ''; 刷画册底();
  const 任务 = (async () => {
    try {
      const api = window.pywebview && window.pywebview.api;
      if (!api || !api.gallery_list) throw new Error('画册还没连接上，请稍后重试');
      const r = await 画册限时(api.gallery_list(...参数));
      if (代 !== 画册.请求) return;
      if (!r || !r.ok) throw new Error((r && r.error) || '作品暂时没取回来');
      if (!Array.isArray(r.画)) throw new Error('画册返回的内容不完整');
      const 已有 = new Set(画册.画.map(x => x.名));
      for (const x of r.画) {
        if (!x || typeof x.名 !== 'string') throw new Error('画册返回的内容不完整');
        if (已有.has(x.名)) throw new Error('续取返回了重复作品，请刷新画册');
        已有.add(x.名);
      }
      if (r.还有 && (!r.画.length || JSON.stringify(r.下个) === JSON.stringify(续))) {
        throw new Error('续取位置没有更新，请刷新画册');
      }
      画册.总 = Number(r.总) || 0; 画册.全册总 = Number(r.全册总) || 画册.总;
      画册.月 = r.月 || {}; 画册.最早 = r.最早 || 0; 画册.最新 = r.最新 || 0;
      r.画.forEach(x => { 投一张(x); 画册.画.push(x); });
      画册.已取 += r.画.length;
      画册.游标 = r.下个 || null;
      画册.完了 = typeof r.还有 === 'boolean' ? !r.还有 : (!r.画.length || 画册.已取 >= 画册.总);
      刷画册计数();
      if (本次重置 || !画册.版本) {
        // 只有新列表已经成功应用，才推进确认基线并消除更新提示。
        画册.版本 = r.版本 || 画册.版本;
        画册.有更新 = false;
        $('#galNotice').hidden = true;
      } else if (r.变化) {
        提示画册更新();
      }
    } catch (e) {
      if (代 === 画册.请求) 画册.错误 = e.message || '作品暂时没取回来';
    } finally {
      if (代 === 画册.请求) { 画册.取着 = false; 画册.任务 = null; 刷画册底(); }
    }
  })();
  画册.任务 = 任务;
  return 任务;
}

function 刷新画册() { return 取一批(true); }

function 画册查一批() {
  if (!$('.chat').classList.contains('on-gallery') || document.hidden || $('#galViewer').open ||
      画册.错误 || 画册.操作.size || 画册.取着 || 画册.完了 || 画册.待搜索 || !画册.画.length) return;
  const 滚 = $('#galScroll');
  if (滚.scrollHeight - 滚.scrollTop - 滚.clientHeight < 460) 取一批(false);
}

async function 核对画册() {
  if (!画册.版本 || 画册.取着 || 画册.核对中 || Date.now() - 画册.核对时 < 2000 ||
      !$('.chat').classList.contains('on-gallery') || document.hidden) return;
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.gallery_status) return;
  画册.核对时 = Date.now();
  画册.核对中 = true;
  const 代 = 画册.请求, 版 = 画册.版本;
  try {
    const r = await 画册限时(api.gallery_status(
      画册.收纳, 画册.词, 画册.月份, 画册.排序, 版
    ));
    if (代 === 画册.请求 && r && r.ok && r.版本 !== 版) 提示画册更新();
  } catch (_) {}
  finally { 画册.核对中 = false; }
}

function 加载画册() {
  if (!画册.进过) return 刷新画册();
  核对画册();
}

function 切画廊(在) {
  const c = $('.chat');
  c.classList.toggle('on-gallery', !!在);
  if (!在) return;
  c.classList.remove('on-empty', 'on-diary');
  const 名 = $('.chat-head .hname');
  if (名 && 名.firstChild) 名.firstChild.nodeValue = '画画工坊 ';
  $('.chat-head .hname .tag').textContent = '她的画';
  $('#chatSub').textContent = '作品画册';
  加载画册();
}

function 拿掉画册一张(x) {
  画册.请求++; 画册.取着 = false; 画册.任务 = null; 画册.错误 = '';
  画册.画 = 画册.画.filter(y => y.名 !== x.名);
  画册.总 = Math.max(0, 画册.总 - 1); 画册.全册总 = Math.max(0, 画册.全册总 - 1);
  画册.已取 = Math.max(0, 画册.已取 - 1);
  const 月 = 月键(x.时);
  画册.月[月] = Math.max(0, (画册.月[月] || 0) - 1);
  if (!画册.月[月]) delete 画册.月[月];
  重排画册(); 刷画册计数(); 刷画册底();
  if (!画册.画.length && !画册.完了) 取一批(false);
}

function 开始画册操作(键) {
  画册.操作.add(键);
  // 作废已经在路上的列表，防止其总数与本地收纳结果重复结算。
  画册.请求++; 画册.取着 = false; 画册.任务 = null;
  刷画册底();
}

function 结束画册操作(键) {
  画册.操作.delete(键); 刷画册底();
  if (画册.操作.size) return;
  if (画册.待重取) { 画册.待重取 = false; 刷新画册(); }
  else if (!画册.画.length && !画册.完了) 取一批(false);
  else 画册查一批();
}

async function 移动作品(x, 钮) {
  const 键 = String(x.收纳) + x.名;
  if (画册.操作.has(键)) return;
  const api = window.pywebview && window.pywebview.api, fn = x.收纳 ? 'gallery_restore' : 'gallery_del';
  if (!api || !api[fn]) { toast('画册还没连接上'); return; }
  开始画册操作(键); 钮.disabled = true;
  const 条件 = 画册条件(), 位 = 画册.画.findIndex(y => y.名 === x.名);
  try {
    const r = await api[fn](x.名);
    if (!r || !r.ok) throw new Error((r && r.error) || '这次没能完成');
    if (!x.收纳) {
      画册.撤销 = {名: r.收纳名, 画: x, 条件, 位};
      $('#galUndo').hidden = false;
    } else if (画册.撤销 && 画册.撤销.名 === x.名) {
      画册.撤销 = null; $('#galUndo').hidden = true;
    }
    if (条件 === 画册条件() && 画册.画.some(y => y.名 === x.名)) 拿掉画册一张(x);
    else 提示画册更新();
    toast(x.收纳 ? '已恢复到画册' : '已移入收纳箱，可以撤销');
  } catch (e) { toast(e.message || '这次没能完成'); }
  finally {
    结束画册操作(键); 钮.disabled = false;
    if (画册.收纳 === !!x.收纳) {
      const 项 = [...$('#galBody').querySelectorAll('.gal-item')].find(n => n.dataset.n === x.名);
      if (项) 项.querySelector('.gal-card-foot button').disabled = false;
    }
  }
}

async function 撤销画册移动() {
  const 记 = 画册.撤销, 钮 = $('#galUndoBtn'), api = window.pywebview && window.pywebview.api;
  if (!记 || !api || !api.gallery_restore || 钮.disabled) return;
  const 键 = '撤销:' + 记.名;
  开始画册操作(键); 钮.disabled = true;
  try {
    const r = await api.gallery_restore(记.名);
    if (!r || !r.ok) throw new Error((r && r.error) || '没能恢复');
    if (记.条件 === 画册条件() && !画册.收纳) {
      const x = {...记.画, 名: r.名, 原名: r.名};
      if (!画册.画.some(y => y.名 === x.名)) {
        画册.画.splice(Math.min(记.位, 画册.画.length), 0, x);
        画册.总++; 画册.全册总++; 画册.已取++;
        const 月 = 月键(x.时); 画册.月[月] = (画册.月[月] || 0) + 1;
        重排画册(); 刷画册计数(); 刷画册底();
      }
    } else 提示画册更新();
    if (画册.撤销 === 记) { 画册.撤销 = null; $('#galUndo').hidden = true; }
    toast('作品恢复了');
  } catch (e) { toast(e.message || '没能恢复'); }
  finally { 结束画册操作(键); }
}

function 画册缩放(倍 = 1, 横 = 0, 纵 = 0) {
  const im = $('#galViewImg'), 台 = $('#galViewStage');
  画册.倍 = Math.min(8, Math.max(1, 倍));
  const 横界 = Math.max(0, (im.offsetWidth * 画册.倍 - 台.clientWidth) / 2);
  const 纵界 = Math.max(0, (im.offsetHeight * 画册.倍 - 台.clientHeight) / 2);
  画册.横 = Math.min(横界, Math.max(-横界, 横)); 画册.纵 = Math.min(纵界, Math.max(-纵界, 纵));
  im.style.transform = 'translate(' + 画册.横 + 'px,' + 画册.纵 + 'px) scale(' + 画册.倍 + ')';
  $('#galZoomLevel').textContent = 画册.倍 === 1 ? '适应' :
    Math.round(im.offsetWidth / (im.naturalWidth || im.offsetWidth || 1) * 画册.倍 * 100) + '%';
  $('#galZoomOut').disabled = 画册.倍 <= 1; $('#galZoomIn').disabled = 画册.倍 >= 8;
  台.classList.toggle('zoomed', 画册.倍 > 1);
}

function 刷画册预览导航() {
  const i = 画册.画.findIndex(x => x.名 === 画册.预览);
  $('#galPrev').disabled = i <= 0 || 画册.翻着;
  $('#galNext').disabled = 画册.翻着 || (i >= 画册.画.length - 1 && 画册.完了);
  $('#galViewIndex').textContent = (i + 1) + ' / ' + 画册.总;
}

function 开画册预览(名) {
  const x = 画册.画.find(y => y.名 === 名);
  if (!x) return;
  const box = $('#galViewer'), im = $('#galViewImg');
  画册.预览 = 名;
  $('#galViewName').textContent = x.原名;
  const 大小 = x.字节 >= 1048576 ? (x.字节 / 1048576).toFixed(1) + ' MB' : Math.ceil(x.字节 / 1024) + ' KB';
  $('#galViewMeta').textContent = new Date(x.时 * 1000).toLocaleString('zh-CN') +
    ' · ' + (x.宽 && x.高 ? x.宽 + ' × ' + x.高 + ' · ' : '') + 大小;
  const 状 = $('#galViewStatus');
  状.hidden = false; 状.textContent = '正在载入原图…';
  im.hidden = false; im.src = 原图址(x);
  if (!box.open) box.showModal();
  画册缩放(); 刷画册预览导航();
}

async function 翻画册预览(方向) {
  if (画册.翻着 || !$('#galViewer').open) return;
  const 当前 = 画册.预览;
  let i = 画册.画.findIndex(x => x.名 === 当前);
  if (方向 > 0 && i === 画册.画.length - 1 && !画册.完了) {
    画册.翻着 = true; 刷画册预览导航();
    try { await (画册.取着 ? 画册.任务 : 取一批(false)); }
    finally { 画册.翻着 = false; }
    if (!$('#galViewer').open || 当前 !== 画册.预览) return;
    i = 画册.画.findIndex(x => x.名 === 当前);
  }
  const x = 画册.画[i + 方向];
  if (x) 开画册预览(x.名);
  else if (画册.错误) toast(画册.错误);
  刷画册预览导航();
}

document.addEventListener('DOMContentLoaded', () => {
  let 搜表 = 0, 重排表 = 0, 宽 = 0;
  const 延后搜索 = () => {
    画册.请求++; 画册.待搜索 = true; 画册.取着 = false;
    画册.词 = $('#galSearch').value.trim(); $('#galClear').hidden = !$('#galSearch').value;
    clearTimeout(搜表);
    搜表 = setTimeout(() => { 画册.待搜索 = false; 刷新画册(); }, 240);
  };
  $('#galSearch').addEventListener('input', e => { if (!e.isComposing) 延后搜索(); });
  $('#galSearch').addEventListener('compositionend', 延后搜索);
  $('#galSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.isComposing) { clearTimeout(搜表); 刷新画册(); }
  });
  $('#galClear').addEventListener('click', () => {
    $('#galSearch').value = ''; 画册.词 = ''; clearTimeout(搜表); 刷新画册(); $('#galSearch').focus();
  });
  $('#galSort').addEventListener('change', e => { clearTimeout(搜表); 画册.排序 = e.target.value; 刷新画册(); });
  $('#galMonth').addEventListener('change', e => { clearTimeout(搜表); 画册.月份 = e.target.value; 刷新画册(); });
  $('#galGroup').addEventListener('change', e => { 画册.分组 = e.target.checked; 重排画册(); });
  const 切位置 = 收纳 => {
    if (画册.收纳 === 收纳) return;
    clearTimeout(搜表); 画册.收纳 = 收纳;
    for (const [id, 在] of [['galMainTab', !收纳], ['galTrashTab', 收纳]]) {
      $('#' + id).setAttribute('aria-selected', String(在)); $('#' + id).tabIndex = 在 ? 0 : -1;
    }
    $('#galScroll').setAttribute('aria-labelledby', 收纳 ? 'galTrashTab' : 'galMainTab');
    刷新画册();
  };
  $('#galMainTab').addEventListener('click', () => 切位置(false));
  $('#galTrashTab').addEventListener('click', () => 切位置(true));
  $('.gal-tabs').addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault();
    切位置(e.key === 'End' || (e.key !== 'Home' && !画册.收纳));
    $(画册.收纳 ? '#galTrashTab' : '#galMainTab').focus();
  });
  $('#galRefresh').addEventListener('click', () => { clearTimeout(搜表); 刷新画册(); });
  $('#galUpdate').addEventListener('click', () => { clearTimeout(搜表); 刷新画册(); });
  $('#galUndoBtn').addEventListener('click', 撤销画册移动);
  $('#galScroll').addEventListener('scroll', 画册查一批, {passive: true});
  const 尺寸变化 = () => {
    const 新宽 = $('#galBody').clientWidth;
    if (!新宽 || Math.abs(新宽 - 宽) < 2) return;
    宽 = 新宽; clearTimeout(重排表);
    重排表 = setTimeout(() => { if (画册.画.length) 重排画册(); }, 160);
  };
  if (window.ResizeObserver) new ResizeObserver(尺寸变化).observe($('#galScroll'));
  window.addEventListener('resize', () => { 尺寸变化(); if ($('#galViewer').open) 画册缩放(); });
  window.addEventListener('focus', 核对画册);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) 核对画册(); });
  window.addEventListener('pywebviewready', () => {
    if ($('.chat').classList.contains('on-gallery') && !画册.画.length) 刷新画册();
  });
  setInterval(画册查一批, 400);
  setInterval(核对画册, 12000);

  const 选 = document.createElement('input');
  选.type = 'file'; 选.accept = 'image/png,image/jpeg,image/gif,image/webp'; 选.multiple = true; 选.hidden = true;
  document.body.appendChild(选);
  $('#galAdd').addEventListener('click', () => 选.click());
  选.addEventListener('change', async () => {
    const 文件 = [...选.files]; 选.value = '';
    const api = window.pywebview && window.pywebview.api, 钮 = $('#galAdd'), 文 = 钮.querySelector('span');
    if (!文件.length || 钮.disabled) return;
    if (!api || !api.gallery_add) { toast('画册还没连接上'); return; }
    钮.disabled = true;
    let 新 = 0, 重 = 0, 错 = [];
    try {
      for (let i = 0; i < 文件.length; i++) {
        文.textContent = (i + 1) + ' / ' + 文件.length;
        try {
          if (文件[i].size > 100 * 1024 * 1024) throw new Error('超过 100 MB');
          const 果 = await 读原图(文件[i]);
          const r = await api.gallery_add(果.名, String(果.全).split(',')[1] || '');
          if (!r || !r.ok) throw new Error((r && r.error) || '没能保存');
          if (r.重复) 重++; else 新++;
        } catch (e) { 错.push(文件[i].name + '：' + e.message); }
      }
      if (新) {
        if (!画册.收纳 && !$('#galViewer').open && $('.chat').classList.contains('on-gallery')) await 刷新画册();
        else 提示画册更新();
      }
      toast(错.length ? ('已添加 ' + 新 + ' 张；' + 错.join('；')) :
        ('已添加 ' + 新 + ' 张' + (重 ? '，跳过 ' + 重 + ' 张重复作品' : '')));
    } finally { 钮.disabled = false; 文.textContent = '添加作品'; }
  });
  $('#galCollect').addEventListener('click', async () => {
    const api = window.pywebview && window.pywebview.api, 钮 = $('#galCollect'), 文 = 钮.querySelector('span');
    if (!api || !api.gallery_collect) { toast('画册还没连接上'); return; }
    钮.disabled = true; 文.textContent = '正在收取…';
    try {
      const r = await api.gallery_collect();
      if (!r || !r.ok) throw new Error((r && r.error) || '这次没能收取');
      if (r.收了) {
        if (!画册.收纳 && !$('#galViewer').open && $('.chat').classList.contains('on-gallery')) await 刷新画册();
        else 提示画册更新();
      }
      toast(r.收了 ? '收进画册 ' + r.收了 + ' 张' : '没有新的作品');
    } catch (e) { toast(e.message || '这次没能收取'); }
    finally { 钮.disabled = false; 文.textContent = '从聊天收取'; }
  });

  const box = $('#galViewer'), im = $('#galViewImg'), 台 = $('#galViewStage');
  $('#galViewClose').addEventListener('click', () => box.close());
  box.addEventListener('close', () => {
    画册.预览 = null; im.removeAttribute('src'); 画册缩放();
  });
  im.addEventListener('load', () => {
    const 状 = $('#galViewStatus'), 需还焦 = 状.contains(document.activeElement);
    状.hidden = true; 画册缩放();
    if (需还焦) $('#galViewClose').focus({preventScroll: true});
  });
  im.addEventListener('error', () => {
    if (!box.open || !im.hasAttribute('src')) return;
    im.hidden = true;
    const 状 = $('#galViewStatus');
    状.hidden = false; 状.textContent = '原图暂时打不开，文件可能被移动了。';
    状.appendChild(画册按钮('重新加载', () => 开画册预览(画册.预览)));
  });
  $('#galPrev').addEventListener('click', () => 翻画册预览(-1));
  $('#galNext').addEventListener('click', () => 翻画册预览(1));
  $('#galZoomOut').addEventListener('click', () => 画册缩放(画册.倍 / 1.25, 画册.横, 画册.纵));
  $('#galZoomIn').addEventListener('click', () => 画册缩放(画册.倍 * 1.25, 画册.横, 画册.纵));
  $('#galZoomFit').addEventListener('click', () => 画册缩放());
  台.addEventListener('wheel', e => {
    e.preventDefault(); 画册缩放(画册.倍 * (e.deltaY < 0 ? 1.15 : 1 / 1.15), 画册.横, 画册.纵);
  }, {passive: false});
  台.addEventListener('dblclick', () => 画册缩放(画册.倍 > 1 ? 1 : 2));
  let 拖 = null;
  台.addEventListener('pointerdown', e => {
    if (e.button !== 0 || 画册.倍 <= 1 || e.target !== im) return;
    拖 = {x: e.clientX - 画册.横, y: e.clientY - 画册.纵};
    台.setPointerCapture(e.pointerId); 台.classList.add('dragging');
  });
  台.addEventListener('pointermove', e => { if (拖) 画册缩放(画册.倍, e.clientX - 拖.x, e.clientY - 拖.y); });
  const 停拖 = () => { 拖 = null; 台.classList.remove('dragging'); };
  台.addEventListener('pointerup', 停拖); 台.addEventListener('pointercancel', 停拖);
  document.addEventListener('keydown', e => {
    if (!box.open || e.key !== 'Tab') return;
    const 钮们 = [...box.querySelectorAll('button:not(:disabled),a[href]')].filter(n => n.getClientRects().length);
    if (!钮们.length) return;
    const i = 钮们.indexOf(document.activeElement);
    if (i < 0 || (e.shiftKey && i === 0) || (!e.shiftKey && i === 钮们.length - 1)) {
      e.preventDefault(); 钮们[e.shiftKey ? 钮们.length - 1 : 0].focus();
    }
  });
  box.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault(); 翻画册预览(e.key === 'ArrowLeft' ? -1 : 1);
    } else if (e.key === '+' || e.key === '=') {
      e.preventDefault(); 画册缩放(画册.倍 * 1.25, 画册.横, 画册.纵);
    } else if (e.key === '-') {
      e.preventDefault(); 画册缩放(画册.倍 / 1.25, 画册.横, 画册.纵);
    } else if (e.key === '0') 画册缩放();
  });
  $('#galSave').addEventListener('click', async () => {
    const x = 画册.画.find(y => y.名 === 画册.预览), 钮 = $('#galSave');
    if (!x || 钮.disabled) return;
    const api = window.pywebview && window.pywebview.api;
    if (api && api.gallery_export) {
      钮.disabled = true;
      try {
        const r = await api.gallery_export(x.名, !!x.收纳);
        if (!r || !r.ok) toast((r && r.error) || '原图没能保存');
        else if (!r.取消) toast('原图已保存');
      } catch (_) { toast('原图没能保存'); }
      finally { 钮.disabled = false; }
    } else {
      const a = document.createElement('a'); a.href = 原图址(x); a.download = x.原名;
      document.body.appendChild(a); a.click(); a.remove();
    }
  });
});
