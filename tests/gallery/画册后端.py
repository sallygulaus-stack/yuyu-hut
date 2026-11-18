import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "panel"))
"""画册回归：所有写入都在系统临时目录，不初始化面板、不碰真实聊天。"""
import base64
import concurrent.futures
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image


项目 = Path(__file__).resolve().parents[2]
if str(项目) not in sys.path:
    sys.path.insert(0, str(项目))
from tests.support import IsolatedPanel


def 图字节(颜色='red', 大小=(64, 48), 格式='PNG'):
    出 = io.BytesIO()
    Image.new('RGB', 大小, 颜色).save(出, format=格式)
    return 出.getvalue()


class 画册回归(unittest.TestCase):
    def setUp(self):
        global 面板
        self.夹具 = IsolatedPanel(项目, 'gallery_test_panel')
        self.根 = self.夹具.root
        面板, self.api = self.夹具.panel, self.夹具.api

    def tearDown(self):
        self.夹具.close()

    def 加(self, 名='作品.png', data=None):
        return self.api.gallery_add(名, base64.b64encode(data or 图字节()).decode())

    def test_原字节与实际格式(self):
        data = 图字节('blue', 格式='JPEG')
        r = self.加('作品 #100% .png', data)
        self.assertTrue(r['ok'], r)
        self.assertTrue(r['名'].endswith('.jpg'))
        self.assertIn('#100%', r['名'])
        self.assertEqual((Path(面板.画册目录) / r['名']).read_bytes(), data)

    def test_拒绝坏图而不留文件(self):
        for b64 in ('%%%broken', '', base64.b64encode(b'not an image').decode(),
                    base64.b64encode(图字节()[:35]).decode(),
                    base64.b64encode(图字节(格式='JPEG')[:-100]).decode()):
            self.assertFalse(self.api.gallery_add('坏图.png', b64)['ok'])
        self.assertEqual(os.listdir(面板.画册目录), [])

    def test_并发上传同图只保存一张(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as 池:
            果 = list(池.map(lambda _: self.加('同名.png'), range(16)))
        self.assertTrue(all(r['ok'] for r in 果), 果)
        self.assertEqual(sum(not r['重复'] for r in 果), 1)
        self.assertEqual(self.api.gallery_status()['总'], 1)
        self.assertFalse(any(n.endswith('.part') for n in os.listdir(面板.画册目录)))

    def test_并发同名不同图不覆盖(self):
        图 = [图字节((i * 29, 17, 211)) for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as 池:
            果 = list(池.map(lambda data: self.加('撞名.png', data), 图))
        self.assertTrue(all(r['ok'] for r in 果), 果)
        self.assertEqual(len({r['名'] for r in 果}), 8)
        self.assertEqual({p.read_bytes() for p in Path(面板.画册目录).iterdir()}, set(图))

    def test_收纳重传恢复都不丢原图(self):
        data = 图字节()
        r = self.加(data=data)
        收 = self.api.gallery_del(r['名'])
        self.assertTrue(收['ok'], 收)
        self.assertEqual(self.api.gallery_status()['总'], 0)
        self.assertEqual(self.api.gallery_list(0, 9, '新', '', '', None, True)['总'], 1)
        self.assertEqual((Path(面板.画册收纳目录) / 收['收纳名']).read_bytes(), data)
        新 = self.加(data=data)
        self.assertFalse(新['重复'], 新)
        恢 = self.api.gallery_restore(收['收纳名'])
        self.assertTrue(恢['ok'], 恢)
        self.assertNotEqual(恢['名'], 新['名'])
        self.assertEqual((Path(面板.画册目录) / 恢['名']).read_bytes(), data)
        self.assertEqual(self.api.gallery_status()['总'], 2)
        self.assertFalse(self.api.gallery_restore(收['收纳名'])['ok'])

    def test_手改文件后缓存会核对(self):
        r = self.加()
        路 = Path(面板.画册目录) / r['名']
        self.assertTrue(self.加()['重复'])
        旧版 = self.api.gallery_status()['版本']
        路.unlink()
        self.assertEqual(self.api.gallery_status()['总'], 0)
        self.assertNotEqual(self.api.gallery_status()['版本'], 旧版)
        self.assertFalse(self.加()['重复'])
        路.write_bytes(图字节('blue', (120, 80)))
        包 = self.api.gallery_list()
        self.assertEqual((包['画'][0]['宽'], 包['画'][0]['高']), (120, 80))
        self.assertFalse(self.加()['重复'])

    def test_稳定翻页与相同时间排序(self):
        根 = Path(面板.画册目录)
        for i in range(10):
            p = 根 / ('作品%02d.png' % i)
            p.write_bytes(图字节((i * 20, 0, 0)))
            os.utime(p, ns=(1794000000000000000, 1794000000000000000))
        第一 = self.api.gallery_list(0, 3)
        self.assertEqual([x['名'] for x in 第一['画']], ['作品09.png', '作品08.png', '作品07.png'])
        根.joinpath('刚进来的.png').write_bytes(图字节('green'))
        根.joinpath('作品08.png').unlink()
        第二 = self.api.gallery_list(3, 3, '新', '', '', 第一['下个'], False, 第一['版本'])
        self.assertEqual([x['名'] for x in 第二['画']], ['作品06.png', '作品05.png', '作品04.png'])
        self.assertTrue(第二['变化'])
        全 = list(第一['画']) + list(第二['画'])
        续 = 第二['下个']
        while True:
            页 = self.api.gallery_list(0, 3, '新', '', '', 续)
            全.extend(页['画'])
            if not 页['还有']:
                break
            续 = 页['下个']
        self.assertEqual(len({x['名'] for x in 全}), 10)
        self.assertNotIn('刚进来的.png', [x['名'] for x in 全])
        错 = self.api.gallery_list(0, 3, '旧', '', '', 第一['下个'])
        self.assertFalse(错['ok'])

    def test_年月筛选与日期边界不受排序影响(self):
        根 = Path(面板.画册目录)
        for 名, 日期 in [('Sun #1.png', '2026-01-03'), ('sun 2.png', '2026-10-04'),
                          ('Moon.png', '2026-11-15')]:
            p = 根 / 名
            p.write_bytes(图字节())
            秒 = time.mktime(time.strptime(日期, '%Y-%m-%d'))
            os.utime(p, (秒, 秒))
        新 = self.api.gallery_list(0, 9, '新')
        旧 = self.api.gallery_list(0, 9, '旧')
        self.assertEqual((新['最早'], 新['最新']), (旧['最早'], 旧['最新']))
        页 = self.api.gallery_list(0, 9, '新', 'SUN', '2026-10')
        self.assertEqual(页['总'], 1)
        self.assertEqual(页['月'], {'2026-01': 1, '2026-10': 1})
        self.assertEqual(页['画'][0]['名'], 'sun 2.png')

    def test_一万张只解码当页且扫描不占写锁(self):
        根 = Path(面板.画册目录)
        data = 图字节()
        for i in range(10000):
            根.joinpath('%05d.png' % i).write_bytes(data)
        with patch.object(面板, '_图多大', wraps=面板._图多大) as 量, \
             patch.object(面板, '_缩略图', return_value=''):
            开 = time.perf_counter()
            第一 = self.api.gallery_list(0, 9)
            毫秒 = (time.perf_counter() - 开) * 1000
            self.api.gallery_list(0, 9)
            self.assertEqual(量.call_count, 9)
            self.assertEqual(第一['总'], 10000)
        进入, 继续 = threading.Event(), threading.Event()
        真扫 = 面板._扫画册
        def 慢扫(*args):
            进入.set()
            继续.wait(3)
            return 真扫(*args)
        with patch.object(面板, '_扫画册', 慢扫), patch.object(面板, '_缩略图', return_value=''):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as 池:
                任务 = 池.submit(self.api.gallery_list)
                self.assertTrue(进入.wait(2))
                可拿锁 = 面板._画册锁.acquire(timeout=0.1)
                if 可拿锁:
                    面板._画册锁.release()
                继续.set()
                self.assertTrue(可拿锁, '列表扫描阻塞了画册写入')
                self.assertTrue(任务.result()['ok'])
        print(json.dumps({'一万张首批毫秒': round(毫秒, 2), '尺寸解码次数': 量.call_count}, ensure_ascii=False))

    def test_小图并发透明旋转与替换(self):
        源 = self.根 / 'alpha.png'
        Image.new('RGBA', (900, 600), (255, 20, 30, 0)).save(源)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as 池:
            果 = list(池.map(lambda _: 面板._做小图(str(源), 面板.画册缓存目录, 源.name), range(16)))
        self.assertTrue(all(果), 果)
        缓 = Path(面板.画册缓存目录) / 果[0]
        with Image.open(缓) as im:
            self.assertIn('A', im.getbands())
            self.assertEqual(im.getpixel((10, 10))[3], 0)
        时 = 源.stat().st_mtime_ns
        Image.new('RGBA', (100, 200), (0, 20, 30, 255)).save(源)
        os.utime(源, ns=(时, 时))
        面板._做小图(str(源), 面板.画册缓存目录, 源.name)
        with Image.open(缓) as im:
            self.assertEqual(im.size, (100, 200))
        横 = self.根 / 'rotate.jpg'
        exif = Image.Exif(); exif[274] = 6
        Image.new('RGB', (400, 200), 'green').save(横, exif=exif)
        self.assertEqual(面板._图多大(str(横)), (200, 400))
        小 = 面板._做小图(str(横), 面板.画册缓存目录, 横.name)
        with Image.open(Path(面板.画册缓存目录) / 小) as im:
            self.assertEqual(im.size, (200, 400))
        self.assertFalse(any(p.suffix == '.part' for p in Path(面板.画册缓存目录).iterdir()))

    def test_收取计数与收纳不会自动回流(self):
        根 = Path(面板.聊媒体目录)
        for i in range(2):
            根.joinpath('%d.png' % i).write_bytes(图字节((i * 100, 20, 30), (900, 600)))
        根.joinpath('小图.png').write_bytes(图字节())
        记录 = [{'谁': '她', '段': [{'type': 'image', '文件': x} for x in ['0.png', '1.png', '0.png', '小图.png']]}]
        with patch.object(面板, '_聊天索引', return_value=记录):
            self.assertEqual(self.api.gallery_collect()['收了'], 2)
            self.assertEqual(self.api.gallery_collect()['收了'], 0)
            self.api.gallery_del('0.png')
            self.assertEqual(self.api.gallery_collect()['收了'], 0)
            self.assertEqual(self.api.gallery_status()['总'], 1)

    def test_文件边界与保存取消(self):
        外 = self.根 / 'outside.png'
        外.write_bytes(图字节())
        for 名 in ('../outside.png', '..\\outside.png', 'C:outside.png', '.hidden.png', ''):
            self.assertFalse(self.api.gallery_del(名)['ok'])
        self.assertTrue(外.exists())
        r = self.加()
        目标 = self.根 / 'export.png'
        class 假窗口:
            def create_file_dialog(self, *a, **kw):
                return [str(目标)]
        with patch.object(面板.webview, 'windows', [假窗口()]):
            self.assertTrue(self.api.gallery_export(r['名'])['ok'])
        self.assertEqual(目标.read_bytes(), 图字节())
        with patch.object(面板.webview, 'windows', [type('取消窗口', (), {'create_file_dialog': lambda *a, **kw: None})()]):
            self.assertTrue(self.api.gallery_export(r['名'])['取消'])

    def test_媒体归档分支去重与导出实际扩展名(self):
        面板.媒体库 = 面板.MediaArchive(self.根 / 'archive')
        def 编码(fmt, mode='RGB', size=(320, 240), animated=False):
            frames = [Image.new(mode, size, (20, 80, 180))]
            if animated:
                frames.append(Image.new(mode, size, (180, 80, 20)))
            out = io.BytesIO()
            frames[0].save(
                out, format=fmt, save_all=animated,
                append_images=frames[1:] if animated else [],
                duration=60, loop=0,
            )
            return out.getvalue()

        def 导出并验证(name, payload, expect_animated=False):
            added = self.加(name, payload)
            self.assertTrue(added['ok'], added)
            source = 面板.媒体库.resolve_path(added['媒体ID'])
            self.assertTrue(source)
            target = self.根 / ('export-' + added['媒体ID'] + source.suffix)
            class 假窗口:
                def create_file_dialog(self, *args, **kwargs):
                    self.name = kwargs.get('save_filename')
                    return [str(target)]
            window = 假窗口()
            with patch.object(面板.webview, 'windows', [window]):
                result = self.api.gallery_export(added['媒体ID'])
            self.assertTrue(result['ok'], result)
            self.assertEqual(Path(window.name).suffix.lower(), source.suffix.lower())
            with Image.open(target) as decoded:
                self.assertEqual(bool(getattr(decoded, 'is_animated', False)), expect_animated)
                decoded.verify()
            return added

        data = io.BytesIO()
        im = Image.new('RGB', (1200, 900))
        px = im.load()
        for y in range(im.height):
            for x in range(im.width):
                px[x, y] = ((x * 17 + y * 3) % 256, (y * 11) % 256, (x + y) % 256)
        im.save(data, format='PNG')
        raw = data.getvalue()
        first = self.加('归档作品.png', raw)
        again = self.加('同内容换名.jpg', raw)
        self.assertTrue(first['ok'], first)
        self.assertTrue(again['重复'], again)
        self.assertEqual(first['媒体ID'], again['媒体ID'])
        other = self.加('归档作品.png', 图字节('navy', (96, 72)))
        self.assertTrue(other['ok'] and not other['重复'], other)
        item = 面板.媒体库.get_media(first['媒体ID'])
        source = 面板.媒体库.resolve_path(first['媒体ID'])
        self.assertTrue(source and item)
        expected = source.suffix.lower()
        target = self.根 / ('导出' + expected)
        class 假窗口:
            def create_file_dialog(self, *args, **kwargs):
                self.name = kwargs.get('save_filename')
                return [str(target)]
        window = 假窗口()
        with patch.object(面板.webview, 'windows', [window]):
            result = self.api.gallery_export(first['媒体ID'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(Path(window.name).suffix.lower(), expected)
        with Image.open(target) as decoded:
            decoded.verify()
        jpeg = 导出并验证('照片.jpg', 编码('JPEG'), False)
        webp = 导出并验证('原图.webp', 编码('WEBP'), False)
        gif = 导出并验证('动图.gif', 编码('GIF', animated=True), True)
        self.assertTrue(jpeg['媒体ID'] and webp['媒体ID'] and gif['媒体ID'])

        fallback = 面板.MediaArchive(self.根 / 'fallback-archive')
        面板.媒体库 = fallback
        def 原样回退(source_path, media_id, display_name, image_info, settings, metadata):
            with tempfile.NamedTemporaryFile(
                dir=fallback.tmp_dir, suffix='.png', delete=False
            ) as handle:
                temp = Path(handle.name)
            shutil.copyfile(source_path, temp)
            return temp, '.png', 'original-no-gain'
        with patch.object(fallback, '_build_archive', side_effect=原样回退):
            returned = 导出并验证('回退.png', 编码('PNG'), False)
        self.assertEqual(fallback.get_media(returned['媒体ID'])['archive_mode'], 'original-no-gain')
        self.assertEqual(self.api.gallery_status()['总'], 1)

    def test_聊天已有媒体首次收进画册不误报重复(self):
        面板.媒体库 = 面板.MediaArchive(self.根 / 'archive-chat-first')
        data = 图字节('teal', (120, 90))
        item = 面板.媒体库.ingest_bytes(
            data, filename='聊天原图.png', mime='image/png',
            source='chat', retention='temporary',
        )
        面板.媒体库.add_reference(
            item['media_id'], ref_id='chat:first',
            kind='chat', collection='main', permanent=False,
        )

        first = self.加('第一次收进画册.png', data)
        self.assertTrue(first['ok'], first)
        self.assertFalse(first['重复'], first)
        self.assertEqual(self.api.gallery_status()['总'], 1)

        again = self.加('同图再次上传.png', data)
        self.assertTrue(again['ok'], again)
        self.assertTrue(again['重复'], again)
        self.assertEqual(self.api.gallery_status()['总'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
