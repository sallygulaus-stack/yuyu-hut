import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
"""两种画册分支的临时浏览器夹具；合成图片、新数据库和模拟保存窗口。"""
import base64
import functools
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse

from PIL import Image
from unittest.mock import patch

项目 = Path(__file__).resolve().parents[2]
if str(项目) not in sys.path:
    sys.path.insert(0, str(项目))
from tests.support import IsolatedPanel, MediaFingerprint

真实指纹 = MediaFingerprint(项目)
夹具 = IsolatedPanel(项目, 'gallery_browser_panel')
面板, 根, api = 夹具.panel, 夹具.root, 夹具.api
媒体模式 = '--媒体库' in sys.argv
if 媒体模式:
    面板.初始化媒体库()
assert all(Path(getattr(面板, key)).is_relative_to(根) for key in (
    '画册目录', '画册缓存目录', '画册收纳目录', '聊记录目录',
    '聊天归档目录', '聊媒体目录', '聊缩略目录', '媒体归档目录',
))
真实指纹.verify()

def 合成图(path, color, size=(960, 1400)):
    Image.new('RGB', size, color).save(path, format='PNG')

源画册 = 根 / 'source-gallery'
源画册.mkdir()
for i in range(44):
    path = 源画册 / ('样本-%02d.png' % i)
    合成图(path, ((i * 37) % 255, (80 + i * 19) % 255, (140 + i * 11) % 255))
    秒 = time.mktime(time.strptime(
        '2026-11-%02d' % min(28, 1 + i),
        '%Y-%m-%d',
    ))
    os.utime(path, (秒, 秒))
for i, (名, 日) in enumerate([('作品 #100% 特殊文件名.png', '2026-10-01'), ('旧日作品.png', '2026-09-15')]):
    原 = 源画册 / 名
    合成图(原, (26 + i, 150, 110))
    秒 = time.mktime(time.strptime(日, '%Y-%m-%d'))
    os.utime(原, (秒, 秒))
for p in 源画册.iterdir():
    if 媒体模式:
        with patch('media_archive.core._now', return_value=p.stat().st_mtime):
            result = api.gallery_add(p.name, base64.b64encode(p.read_bytes()).decode())
        assert result['ok'] and not result['重复'], result
    else:
        shutil.copy2(p, Path(面板.画册目录) / p.name)
源目录指纹 = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in 源画册.iterdir() if p.is_file()}
源内容指纹 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in 源画册.iterdir() if p.is_file()}
允许 = {'gallery_list', 'gallery_status', 'gallery_add', 'gallery_del', 'gallery_restore',
      'gallery_export', 'gallery_collect'}
外部序号 = 0
导出目录 = 根 / 'exports'
导出目录.mkdir()


class 保存窗口:
    def create_file_dialog(self, *_args, **kwargs):
        return [str(导出目录 / Path(kwargs['save_filename']).name)]


面板.webview.windows = [保存窗口()]


class 接待(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def translate_path(self, path):
        路 = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if 路.startswith('/media/'):
            media_id = 路.removeprefix('/media/')
            small = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get('s') == ['1']
            media = 面板.媒体库.resolve_path(media_id, thumbnail=small) if 面板.媒体库 else None
            return str(media or 根 / '_missing')
        for 前, 目录 in [('画册', 面板.画册目录), ('画册缓存', 面板.画册缓存目录), ('画册收纳', 面板.画册收纳目录)]:
            头 = '/assets/' + 前 + '/'
            if 路.startswith(头):
                名 = 路[len(头):]
                return os.path.join(目录, 名 if 名 == os.path.basename(名) and '\\' not in 名 else '_invalid')
        return super().translate_path(path)

    def do_POST(self):
        global 外部序号
        方法 = urllib.parse.unquote(self.path).removeprefix('/__gallery/')
        try:
            参数 = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if 方法 in 允许:
                果 = getattr(api, 方法)(*参数)
            elif 方法 == '外部改动':
                外部序号 += 1
                出 = io.BytesIO()
                Image.new('RGB', (960, 600), (26 + 外部序号, 150, 110)).save(出, format='PNG')
                名 = '验收中新来的' + ('' if 外部序号 == 1 else str(外部序号)) + '.png'
                新时 = max(time.time(), max(p.stat().st_mtime for p in 源画册.iterdir())) + 外部序号
                if 媒体模式:
                    with patch('media_archive.core._now', return_value=新时):
                        果 = api.gallery_add(名, base64.b64encode(出.getvalue()).decode())
                else:
                    新图 = Path(面板.画册目录, 名)
                    新图.write_bytes(出.getvalue())
                    os.utime(新图, (新时, 新时))
                    果 = {'ok': True, '名': 名}
            elif 方法 == '上传样本':
                出 = io.BytesIO()
                Image.new('RGBA', (720, 960), (160, 45, 100, 180)).save(出, format='PNG')
                果 = {'ok': True, 'b64': base64.b64encode(出.getvalue()).decode()}
            elif 方法 == '核对原件':
                现 = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in 源画册.iterdir() if p.is_file()}
                印 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in 源画册.iterdir() if p.is_file()}
                果 = {'ok': 现 == 源目录指纹 and 印 == 源内容指纹, '原图数': len(源目录指纹),
                     '真实媒体': 真实指纹.verify(), '媒体库': 媒体模式, '隔离': True}
            elif 方法 == '导出结果':
                果 = {'ok': True, '文件': []}
                for path in 导出目录.iterdir():
                    with Image.open(path) as image:
                        image.load()
                        果['文件'].append({'名': path.name, '格式': image.format,
                                         '宽': image.width, '高': image.height})
            elif 方法 == '结束验收':
                果 = {'ok': True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                raise ValueError('测试服务没有这个接口')
            data = json.dumps(果, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(400, 'Invalid test request', str(e))


if __name__ == '__main__':
    服务 = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(接待, directory=str(项目 / 'panel' / 'web')))
    服务.daemon_threads = True
    print(json.dumps({'端口': 服务.server_address[1], '媒体库': 媒体模式,
                      '隔离': True}, ensure_ascii=False), flush=True)
    try:
        服务.serve_forever()
    finally:
        服务.server_close()
        try:
            夹具.close()
        finally:
            print('REAL_MEDIA_AUDIT ' + json.dumps(真实指纹.verify()), flush=True)
