"""Chat panel storage, pagination and gallery, extracted from the current application."""
import base64
import collections
import hashlib
import http.server
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import types
import urllib.parse
import uuid

from media_archive import MediaArchive, MediaArchiveError
from diary import Diary
from mobile_chat import MobileChat
from song_import import import_song, with_imports
from settings import default_config, user_data_dir
from runtime import PanelRuntime

BASE = str(Path(__file__).resolve().parent)
WEB_DIR = str(Path(BASE) / "web")
CONFIG = default_config()
DATA_DIR = user_data_dir() / "demo"
webview = types.SimpleNamespace(windows=[], FileDialog=types.SimpleNamespace(SAVE=1))
WIN = {"w": None}
媒体库 = None
界面端口 = None
_server = None
_媒体迁移状态 = {"状态": "未开始", "结果": None, "错误": ""}
画册缩略宽 = 560
图后缀们 = (".png", ".jpg", ".jpeg", ".gif", ".webp")
歌后缀们 = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus")
头像默认 = "assets/default-peer.png"
_记录锁 = threading.RLock()
_聊天文件锁 = threading.RLock()
_聊天索引锁 = threading.RLock()
_聊天计数锁 = threading.RLock()
_聊天索引缓存 = {"签名": None, "条": ()}
_聊天总数 = None
_聊天计数签名 = None
_聊天归档中 = False
_来信 = collections.deque(maxlen=300)
_来信锁 = threading.Lock()
_来信序 = 0


def configure_data(root, config=None):
    global DATA_DIR, CONFIG
    DATA_DIR = Path(root).expanduser().resolve()
    if config is not None:
        CONFIG = config
    paths = {
        "画册目录": "gallery", "画册缓存目录": "gallery-thumbs",
        "画册收纳目录": "stored", "聊记录目录": "chat",
        "聊天归档目录": "chat/history", "聊记录文件": "chat/消息.jsonl", "聊媒体目录": "chat/media",
        "聊缩略目录": "chat/thumbs", "媒体归档目录": "archive",
        "表情包目录": "stickers", "食物目录": "food", "歌目录": "songs",
        "歌单文件": "songs/playlist.json", "头像文件": "avatar.json",
        "SKIN_FILE": "theme.txt",
    }
    for key, relative in paths.items():
        globals()[key] = str(DATA_DIR / relative)


configure_data(DATA_DIR)

def _读歌词(条, 名):
    根 = os.path.normpath(歌目录)
    路们 = []
    写 = str((条 or {}).get('歌词') or '').strip()
    if 写:
        路们.append(写)
    路们.append(os.path.join('歌词', os.path.splitext(名)[0] + '.lrc'))
    for 相 in 路们:
        全 = os.path.normpath(os.path.join(根, 相))
        if not 全.startswith(根 + os.sep) or not os.path.isfile(全):
            continue
        try:
            with open(全, 'r', encoding='utf-8-sig') as f:
                return f.read()[:20000]
        except Exception:
            continue
    return ''

def _图多大(全):
    try:
        from PIL import Image
        with Image.open(全) as im:
            w, h = im.size
            return (h, w) if im.getexif().get(274) in (5, 6, 7, 8) else (w, h)
    except Exception:
        return (0, 0)

def _小图名(名):
    底, 后缀 = os.path.splitext(名)
    return 底 + '__' + 后缀.lstrip('.').lower() + '.webp'
_小图锁组 = tuple((threading.Lock() for _ in range(64)))

def _文件签名(全):
    s = os.stat(全, follow_symlinks=False)
    return (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)

def _做小图(源全, 缓存目录, 名):
    键 = os.path.normcase(os.path.abspath(os.path.join(缓存目录, _小图名(名))))
    with _小图锁组[hash(键) % len(_小图锁组)]:
        return _制作小图(源全, 缓存目录, 名)

def _制作小图(源全, 缓存目录, 名):
    小 = _小图名(名)
    路 = os.path.join(缓存目录, 小)
    临 = 签临 = ''
    try:
        from PIL import Image, ImageOps
        签 = list(_文件签名(源全)) + [画册缩略宽, 2]
        try:
            with open(路 + '.json', encoding='utf-8') as f:
                if json.load(f) == 签 and os.path.isfile(路):
                    return 小
        except (OSError, ValueError):
            pass
        os.makedirs(缓存目录, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=缓存目录, prefix='._', suffix='.part', delete=False) as f:
            临 = f.name
        with Image.open(源全) as im:
            出 = ImageOps.exif_transpose(im)
            出 = 出.convert('RGBA' if 'A' in 出.getbands() or 'transparency' in 出.info else 'RGB')
            出.thumbnail((画册缩略宽, 画册缩略宽))
            出.save(临, 'WEBP', quality=80, method=4)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=缓存目录, prefix='._', suffix='.part', delete=False) as f:
            签临 = f.name
            json.dump(签, f)
        if list(_文件签名(源全)) + [画册缩略宽, 2] != 签:
            return ''
        os.replace(临, 路)
        os.replace(签临, 路 + '.json')
        return 小
    except Exception as e:
        print('[小图] 没做出来 %s：%s' % (名, e), flush=True)
        return ''
    finally:
        for p in (临, 签临):
            if p and os.path.isfile(p):
                os.remove(p)

def _缩略图(名):
    名 = os.path.basename(str(名 or ''))
    if not 名:
        return ''
    原 = os.path.join(画册目录, 名)
    if not os.path.isfile(原):
        return ''
    return _做小图(原, 画册缓存目录, 名)

def _媒体小图(名):
    名 = os.path.basename(str(名 or ''))
    if not 名:
        return ''
    原 = os.path.join(聊媒体目录, 名)
    if not os.path.isfile(原):
        return ''
    return _做小图(原, 聊缩略目录, 名)
_画册锁 = threading.RLock()
_画册缓存锁 = threading.Lock()
_画册文件缓存 = {}
_画册改次 = 0

def _算指纹(全):
    h = hashlib.md5()
    try:
        with open(全, 'rb') as f:
            for 片 in iter(lambda: f.read(1 << 20), b''):
                h.update(片)
    except Exception:
        return ''
    return h.hexdigest()

def _画册原名(名, 收纳=False):
    m = re.match('^\\d{13}_[0-9a-f]{12}__(.+)$', 名) if 收纳 else None
    return m.group(1) if m else 名

def _扫画册(收纳=False):
    根 = 画册收纳目录 if 收纳 else 画册目录
    出 = []
    try:
        with os.scandir(根) as it:
            for f in it:
                if f.name.startswith('.') or not f.name.lower().endswith(图后缀们):
                    continue
                try:
                    if not f.is_file(follow_symlinks=False):
                        continue
                    s = os.stat(f.path, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                签 = (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)
                出.append({'名': f.name, '原名': _画册原名(f.name, 收纳), '时': s.st_mtime, '字节': s.st_size, '收纳': bool(收纳), '_签': 签, '_路': f.path, '版本': hashlib.sha256(repr(签).encode()).hexdigest()[:16]})
    except FileNotFoundError:
        pass
    出.sort(key=lambda x: x['名'])
    签 = hashlib.sha256(repr([(x['名'], x['_签']) for x in 出]).encode('utf-8')).hexdigest()
    with _画册缓存锁:
        在 = {x['_路'] for x in 出}
        for 路 in list(_画册文件缓存):
            if os.path.dirname(路) == 根 and 路 not in 在:
                del _画册文件缓存[路]
    return (出, 签)

def _画册文件信息(x, 键):
    路, 签 = (x['_路'], x['_签'])
    with _画册缓存锁:
        缓 = _画册文件缓存.get(路, {})
        if 缓.get('签') == 签 and 键 in 缓:
            return 缓[键]
    值 = _图多大(路) if 键 == '宽高' else _算指纹(路)
    try:
        if _文件签名(路) != 签:
            return (0, 0) if 键 == '宽高' else ''
    except OSError:
        return (0, 0) if 键 == '宽高' else ''
    with _画册缓存锁:
        缓 = _画册文件缓存.get(路, {})
        if 缓.get('签') != 签:
            缓 = {'签': 签}
            _画册文件缓存[路] = 缓
        缓[键] = 值
    return 值

def _画册查重(印, 字节, 收纳=False):
    for x in _扫画册(收纳)[0]:
        if x['字节'] == 字节 and _画册文件信息(x, '印') == 印:
            return x
    return None

def _画册空名字(目录, 名):
    底, 后 = os.path.splitext(名)
    n, 出 = (1, 名)
    while os.path.exists(os.path.join(目录, 出)):
        n += 1
        出 = '%s-%d%s' % (底, n, 后)
    return 出

def _存入画册(名, data=None, 源全=None, 尊重收纳=False):
    global _画册改次
    os.makedirs(画册目录, exist_ok=True)
    临 = ''
    try:
        with tempfile.NamedTemporaryFile(dir=画册目录, prefix='._', suffix='.part', delete=False) as f:
            临 = f.name
            if data is not None:
                f.write(data)
        if 源全 is not None:
            shutil.copy2(源全, 临)
        印, 字节 = (_算指纹(临), os.path.getsize(临))
        if not 印:
            raise OSError('原图读不出来')
        while True:
            with _画册锁:
                代 = _画册改次
            重 = _画册查重(印, 字节)
            if not 重 and 尊重收纳:
                重 = _画册查重(印, 字节, True)
            with _画册锁:
                if 代 != _画册改次:
                    continue
                if 重:
                    try:
                        if _文件签名(重['_路']) != 重['_签']:
                            continue
                    except FileNotFoundError:
                        continue
                    return {'名': 重['名'], '重复': True, '收纳': 重['收纳']}
                出 = _画册空名字(画册目录, 名)
                try:
                    os.link(临, os.path.join(画册目录, 出))
                except FileExistsError:
                    continue
                _画册改次 += 1
                return {'名': 出, '重复': False}
    finally:
        if 临 and os.path.isfile(临):
            os.remove(临)

def _收画册(源全, 详情=False, 媒体ID='', 强制=False):
    try:
        if 媒体库:
            if not 强制:
                return None
            media_id = str(媒体ID or '').strip()
            item = 媒体库.get_media(media_id) if media_id else None
            if not item and 源全 and os.path.isfile(源全):
                item = 媒体库.ingest_file(源全, source='workshop', retention='permanent', metadata={'source': 'user-gallery'})
                media_id = item.get('media_id') or ''
            if not item or not media_id:
                return None
            w, h = (int(item.get('width') or 0), int(item.get('height') or 0))
            原名 = str(item.get('display_name') or os.path.basename(str(源全 or '新作品')))
            try:
                reference = 媒体库.get_or_create_workshop_reference(media_id, display_name=原名, metadata={'source': 'image-generation' if 强制 else 'chat-image'}, restore_stored=False)
            except Exception:
                return None
            item = 媒体库.get_media(media_id) or item
            果 = {'名': media_id, '媒体ID': media_id, '原名': 原名, '重复': not reference['created'], '收纳': reference['collection'] == 'trash', '宽': w, '高': h, '字节': int(item.get('archive_bytes') or 0)}
            if 详情:
                return 果
            return media_id
        if not os.path.isfile(源全):
            return None
        w, h = _图多大(源全)
        if not (max(w, h) >= 800 and min(w, h) >= 500):
            return None
        底, 后缀 = os.path.splitext(os.path.basename(源全))
        if not 后缀.lower() in 图后缀们:
            后缀 = '.png'
        果 = _存入画册(底[:170] + 后缀, 源全=源全, 尊重收纳=True)
        if 果.get('收纳'):
            return None
        if not 果['重复']:
            _缩略图(果['名'])
            print('[画册] 收了一张 %s（%dx%d）' % (果['名'], w, h), flush=True)
        return 果 if 详情 else 果['名']
    except Exception as e:
        print('[画册] 没收成：', e, flush=True)
        return None

def _画册顺序(x):
    return (x['_签'][1], x['名'].casefold(), x['名'])

def _画册条件签(排序, 搜索, 月份, 收纳):
    return hashlib.sha256(repr((str(排序) not in ('旧', 'old', 'asc'), str(搜索 or '').strip().casefold()[:200], str(月份 or ''), bool(收纳))).encode('utf-8')).hexdigest()[:16]

def _列画册(起=0, 数=0, 排序='新', 搜索='', 月份='', 游标=None, 收纳=False, 版本=''):
    全部, 签 = _扫画册(收纳)
    词 = str(搜索 or '').strip().casefold()[:200]
    月份 = str(月份 or '')
    出 = [x for x in 全部 if 词 in x['原名'].casefold()]
    月计 = {}
    for x in 出:
        键 = time.strftime('%Y-%m', time.localtime(x['时']))
        月计[键] = 月计.get(键, 0) + 1
    if 月份:
        出 = [x for x in 出 if time.strftime('%Y-%m', time.localtime(x['时'])) == 月份]
    总 = len(出)
    最早 = min((x['时'] for x in 出), default=0)
    最新 = max((x['时'] for x in 出), default=0)
    倒 = str(排序) not in ('旧', 'old', 'asc')
    条件 = _画册条件签(排序, 搜索, 月份, 收纳)
    出.sort(key=_画册顺序, reverse=倒)
    版本签 = hashlib.sha256(repr((条件, [(x['名'], x['_签']) for x in 出])).encode('utf-8')).hexdigest()[:16]
    基线 = str(版本 or 版本签)
    if 游标 is not None:
        if not isinstance(游标, dict) or 游标.get('条件') != 条件:
            raise ValueError('浏览条件已经改变，请刷新画册')
        try:
            续 = (int(游标['时']), str(游标['名']).casefold(), str(游标['名']))
        except (KeyError, ValueError, TypeError):
            raise ValueError('续取位置无效，请刷新画册') from None
        基线 = str(游标.get('版本') or 基线)
        出 = [x for x in 出 if (_画册顺序(x) < 续 if 倒 else _画册顺序(x) > 续)]
        起 = 0
    else:
        起 = max(0, int(起))
    页 = 出[起:起 + 数] if 数 else 出[起:]
    下 = {'时': str(页[-1]['_签'][1]), '名': 页[-1]['名'], '条件': 条件, '版本': 基线} if 页 else 游标
    结果 = []
    for x in 页:
        y = {k: v for k, v in x.items() if not k.startswith('_')}
        y['宽'], y['高'] = _画册文件信息(x, '宽高')
        if 数:
            y['小图'] = _做小图(x['_路'], 画册缓存目录, '收纳_' + x['名']) if 收纳 else _缩略图(x['名'])
        结果.append(y)
    return {'页': 结果, '总': 总, '全册总': len(全部), '月': 月计, '最早': 最早, '最新': 最新, '版本': 版本签, '变化': 基线 != 版本签, '浏览状态': '版本已变化' if 基线 != 版本签 else '当前版本', '下个': 下, '还有': bool(数 and len(出) > 起 + len(页))}

def _画册安全名(名):
    名 = str(名 or '')
    if not 名 or 名.startswith('.') or '/' in 名 or ('\\' in 名) or (':' in 名) or (os.path.basename(名) != 名) or (not 名.lower().endswith(图后缀们)):
        raise ValueError('作品文件名无效')
    return 名

def _媒体画册项(收纳=False, 搜索='', 月份='', 排序='新'):
    if not 媒体库:
        return ([], '')
    items = 媒体库.list_workshop('trash' if 收纳 else 'gallery')
    for item in items:
        item['display_name'] = str(item.get('ref_display_name') or item.get('display_name') or item.get('metadata', {}).get('original_filename') or '')
    词 = str(搜索 or '').strip().casefold()[:200]
    月份 = str(月份 or '')
    if 词:
        items = [x for x in items if 词 in str(x.get('display_name') or x.get('metadata', {}).get('original_filename') or '').casefold()]
    if 月份:
        items = [x for x in items if time.strftime('%Y-%m', time.localtime(float(x.get('created_at') or 0))) == 月份]
    倒 = str(排序) not in ('旧', 'old', 'asc')
    items.sort(key=lambda x: (float(x.get('created_at') or 0), str(x.get('media_id') or '')), reverse=倒)
    visible = []
    for item in items:
        fields = (item['media_id'], item.get('created_at'), item['display_name'], item.get('width'), item.get('height'), item.get('archive_bytes'), item.get('extension'), item.get('thumb_bytes'), item.get('retention'))
        item['_版本'] = hashlib.sha256(repr(fields).encode('utf-8')).hexdigest()[:16]
        visible.append(fields)
    version = hashlib.sha256(repr((_画册条件签(排序, 搜索, 月份, 收纳), visible)).encode('utf-8')).hexdigest()[:16]
    return (items, version)

def _归档实际后缀(路径, 媒体项):
    后缀表 = {'PNG': '.png', 'JPEG': '.jpg', 'JPG': '.jpg', 'WEBP': '.webp', 'GIF': '.gif', 'BMP': '.bmp', 'TIFF': '.tiff', 'APNG': '.png'}
    try:
        if MediaArchive:
            fmt = (MediaArchive._image_info(Path(路径)).get('format') or '').upper()
            if fmt in 后缀表:
                return 后缀表[fmt]
    except Exception:
        pass
    ext = os.path.splitext(str(路径))[1].lower()
    if ext:
        return ext
    ext = str((媒体项 or {}).get('extension') or '').lower()
    return ext if ext.startswith('.') else '.bin'

def _图目录(类别):
    return {'表情包': 表情包目录, '食物': 食物目录}.get(str(类别 or ''))

def _列图(目录):
    try:
        return sorted((f for f in os.listdir(目录) if f.lower().endswith(图后缀们) and (not f.startswith('.'))))
    except Exception:
        return []

def _放手删(全):
    try:
        os.chmod(全, 438)
    except Exception:
        pass

def _读头像():
    try:
        with open(头像文件, encoding='utf-8') as f:
            return json.load(f).get('头像') or 头像默认
    except Exception:
        return 头像默认

def 初始化媒体库(目录=None):
    global 媒体库
    if 媒体库 is None and MediaArchive:
        媒体库 = MediaArchive(目录 or 媒体归档目录)
    return 媒体库

def _媒体消息文件们():
    出 = []
    for 根 in (聊记录目录, 聊天归档目录 if '聊天归档目录' in globals() else ''):
        if not 根 or not os.path.isdir(根):
            continue
        try:
            for 路, _, 文件们 in os.walk(根):
                for 名 in 文件们:
                    if 名.lower().endswith('.jsonl') and '媒体归档' not in 路:
                        出.append(os.path.join(路, 名))
        except OSError:
            continue
    return sorted(set(出))

def _媒体后台():
    库 = 初始化媒体库()
    if not 库:
        return
    time.sleep(1.5)
    try:
        _媒体迁移状态['状态'] = '迁移中'
        result = 库.migrate_legacy(message_files=_媒体消息文件们(), media_dirs=(聊媒体目录,), gallery_dirs=(画册目录, 画册收纳目录))
        _媒体迁移状态.update({'状态': '完成' if result.get('status') == 'complete' else '部分失败', '结果': result, '错误': '' if result.get('status') == 'complete' else '迁移未完成：%d 项失败' % len(result.get('failed_items') or [])})
    except Exception as exc:
        _媒体迁移状态.update({'状态': '失败', '错误': str(exc)[:300]})
    try:
        _媒体迁移状态['维护'] = 库.run_maintenance()
        _媒体迁移状态['迁移清理'] = 库.cleanup_migration()
        if 库.migration_cleanup_errors():
            _媒体迁移状态['迁移清理错误'] = 库.migration_cleanup_errors()
        else:
            _媒体迁移状态.pop('迁移清理错误', None)
    except Exception as exc:
        _媒体迁移状态['维护错误'] = str(exc)[:300]
    while True:
        time.sleep(3600)
        try:
            _媒体迁移状态['维护'] = 库.run_maintenance()
            _媒体迁移状态['迁移清理'] = 库.cleanup_migration()
            _媒体迁移状态['维护时间'] = time.time()
            if 库.migration_cleanup_errors():
                _媒体迁移状态['迁移清理错误'] = 库.migration_cleanup_errors()
            else:
                _媒体迁移状态.pop('迁移清理错误', None)
            _媒体迁移状态.pop('维护错误', None)
        except Exception as exc:
            _媒体迁移状态['维护错误'] = str(exc)[:300]

def _媒体段地址(段, 小图=False):
    if not isinstance(段, dict):
        return ''
    media_id = str(段.get('媒体ID') or '').strip()
    if media_id:
        return '/media/' + urllib.parse.quote(media_id) + ('?s=1' if 小图 else '')
    name = os.path.basename(str(段.get('文件') or ''))
    if name:
        return '/media/' + urllib.parse.quote(name) + ('?s=1' if 小图 else '')
    return ''

def _写媒体备用(data, name='', mime=''):
    os.makedirs(聊媒体目录, exist_ok=True)
    ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp', 'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a'}.get(str(mime or '').split(';', 1)[0].lower(), '')
    if not ext:
        ext = os.path.splitext(os.path.basename(str(name or '')))[1].lower() or '.bin'
    if len(ext) > 12 or any((ch in ext for ch in '/\\\x00')):
        ext = '.bin'
    stem = 'pending-%d-%s' % (int(time.time() * 1000), os.urandom(4).hex())
    target = os.path.join(聊媒体目录, stem + ext)
    temp = target + '.part'
    try:
        with open(temp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
        if not os.path.isfile(target) or os.path.getsize(target) != len(data):
            raise OSError('备用媒体写入校验失败')
        with open(target, 'rb') as f:
            if not f.read(1):
                raise OSError('备用媒体不可读取')
        return os.path.basename(target)
    finally:
        try:
            if os.path.isfile(temp):
                os.remove(temp)
        except Exception:
            pass

def _媒体段失败(段):
    if not isinstance(段, dict) or 段.get('type') != 'image':
        return False
    if 段.get('归档失败'):
        return True
    if 段.get('媒体ID'):
        if 媒体库:
            try:
                return not bool(媒体库.resolve_path(str(段['媒体ID'])))
            except Exception:
                return True
        return True
    if 段.get('文件'):
        return not os.path.isfile(os.path.join(聊媒体目录, os.path.basename(str(段['文件']))))
    return True

def _存媒体(段列表, 来源='chat', 保留='temporary', 原件=False):
    出 = []
    for 段 in 段列表 or []:
        if not isinstance(段, dict):
            continue
        条 = dict(段)
        b64 = 条.get('b64')
        if b64:
            try:
                import base64 as _b64
                if isinstance(b64, str) and b64.startswith('data:') and (',' in b64):
                    b64 = b64.split(',', 1)[1]
                data = _b64.b64decode(b64, validate=True)
                name = str(条.get('name') or 条.get('名称') or '')
                if 媒体库:
                    item = 媒体库.ingest_bytes(data, filename=name, mime=str(条.get('mime') or ''), source=str(来源 or 'chat'), retention='permanent' if 保留 == 'permanent' else 'temporary', metadata={'segment_type': str(条.get('type') or ''), 'preserve_original': bool(原件)})
                    ref_id = 媒体库.add_reference(item['media_id'], kind='chat', collection='main', permanent=保留 == 'permanent', display_name=name, metadata={'source': 来源, 'segment_type': str(条.get('type') or '')})
                    条['媒体ID'] = item['media_id']
                    条['引用ID'] = ref_id
                    条['归档'] = True
                    条.pop('b64', None)
                    条.pop('文件', None)
                else:
                    名 = _写媒体备用(data, name, str(条.get('mime') or ''))
                    条['文件'] = 名
                    条.pop('b64', None)
                    条['归档'] = False
                if _媒体段失败(条):
                    raise MediaArchiveError('归档后媒体引用不可解析')
            except Exception as e:
                条['归档失败'] = True
                条['归档错误'] = str(e)[:180]
                try:
                    import base64 as _b64
                    原始 = b64
                    if isinstance(原始, str) and 原始.startswith('data:') and (',' in 原始):
                        原始 = 原始.split(',', 1)[1]
                    data = _b64.b64decode(原始, validate=True)
                    name = str(条.get('name') or 条.get('名称') or '')
                    条['文件'] = _写媒体备用(data, name, str(条.get('mime') or ''))
                    条.pop('b64', None)
                except Exception as fallback_error:
                    条['文件'] = ''
                    条['备用错误'] = str(fallback_error)[:180]
        elif 条.get('媒体ID') and _媒体段失败(条):
            条['归档失败'] = True
            条['归档错误'] = '媒体引用不可解析'
        if 条.get('type') == 'image' and _媒体段失败(条):
            条['归档失败'] = True
            条.setdefault('归档错误', '媒体引用不可解析')
        出.append(条)
    return 出

def _归档指纹():
    try:
        s = os.stat(聊记录文件)
    except FileNotFoundError:
        return ''
    if not s.st_size:
        return ''
    return hashlib.sha256(repr((s.st_ino, s.st_mtime_ns, s.st_size)).encode()).hexdigest()


def _归档记录(确认):
    global _聊天计数签名, _聊天归档中
    with _聊天计数锁:
        _聊天归档中 = True
    try:
        with _聊天文件锁:
            with _记录锁:
                if not 确认 or _归档指纹() != 确认:
                    return ''
                os.makedirs(聊天归档目录, exist_ok=True)
                底 = '消息-%s' % time.strftime('%Y%m%d-%H%M%S')
                全 = os.path.join(聊天归档目录, 底 + '.jsonl')
                n = 2
                while os.path.exists(全):
                    全 = os.path.join(聊天归档目录, '%s-%d.jsonl' % (底, n))
                    n += 1
                os.replace(聊记录文件, 全)
        with _聊天计数锁:
            if _聊天总数 is not None:
                try:
                    _聊天计数签名, _ = _聊天索引签名()
                except Exception:
                    _聊天计数签名 = None
        print('[记录] 归档成 %s' % os.path.basename(全), flush=True)
        return os.path.basename(全)
    finally:
        with _聊天计数锁:
            _聊天归档中 = False

def 记一条(谁, 段列表, conversation_id='main', profile_id='', endpoint_id='',
         message_id='', event_id='', delivery_status='delivered', message=None):
    global _聊天总数, _聊天计数签名
    try:
        os.makedirs(聊记录目录, exist_ok=True)
        瘦 = _存媒体(段列表)
        if any((isinstance(段, dict) and 段.get('type') == 'image' and (段.get('归档失败') or _媒体段失败(段)) for 段 in 瘦)):
            return None
        message_id = str(message_id or ('msg_' + uuid.uuid4().hex))
        event_id = str(event_id or ('evt_' + uuid.uuid4().hex))
        条 = {
            '消息ID': message_id,
            '消息ID_v2': message_id,
            '事件ID': event_id,
            '谁': 谁,
            '段': 瘦,
            '时': time.time(),
            'conversation_id': str(conversation_id or 'main'),
            'profile_id': str(profile_id or ('me' if 谁 == '我' else 'peer')),
            'sender': {
                'profile_id': str(profile_id or ('me' if 谁 == '我' else 'peer')),
                'role': 'me' if 谁 == '我' else 'peer',
                'display_name': '我' if 谁 == '我' else '她',
            },
            'endpoint_id': str(endpoint_id or ''),
            'delivery_status': str(delivery_status or 'delivered'),
            'protocol_version': 'dsh-client/1',
            'capabilities': {'media_ids': True, 'profiles': True, 'delivery_receipts': True},
            'attachments': [str(item.get('媒体ID')) for item in 瘦
                           if isinstance(item, dict) and item.get('媒体ID')],
        }
        if message is not None:
            条.update(message)
            条.update({'消息ID': message['message_id'], '消息ID_v2': message['message_id'],
                       '事件ID': message['event_id'], '时': message['created_at'],
                       '谁': 谁, '段': 瘦, 'segments': 瘦})
        for _ in range(4):
            if _确保聊天总数() is None:
                continue
            with _聊天计数锁:
                try:
                    签, _ = _聊天索引签名()
                except Exception:
                    签 = None
                归档中 = _聊天归档中
                if _聊天总数 is None or (not 归档中 and (签 is None or 签 != _聊天计数签名)):
                    continue
                with _记录锁:
                    if not 归档中:
                        try:
                            签锁, _ = _聊天索引签名()
                        except Exception:
                            签锁 = None
                        if 签锁 != 签 or 签锁 != _聊天计数签名:
                            continue
                    序 = _聊天总数
                    条['序号'] = 序
                    条['local_sequence'] = 序
                    if message is None:
                        条['sequence'] = 序
                    with open(聊记录文件, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(条, ensure_ascii=False) + '\n')
                        f.flush()
                        os.fsync(f.fileno())
                    _聊天总数 = 序 + 1
                    try:
                        _聊天计数签名, _ = _聊天索引签名()
                    except Exception:
                        _聊天计数签名 = None
                    return 序
        with _聊天计数锁:
            with _记录锁:
                序 = len(_聊天索引())
                条['序号'] = 序
                条['local_sequence'] = 序
                if message is None:
                    条['sequence'] = 序
                with open(聊记录文件, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(条, ensure_ascii=False) + '\n')
                    f.flush()
                    os.fsync(f.fileno())
                _聊天总数 = None
                _聊天计数签名 = None
                return 序
    except Exception:
        return None

def _聊天文件们():
    档 = []
    def 顺序(名):
        m = re.fullmatch(r'消息-(\d{8}-\d{6})(?:-(\d+))?\.jsonl', 名)
        return (m[1], int(m[2] or 1)) if m else (名, 0)

    try:
        for 名 in sorted(os.listdir(聊天归档目录), key=顺序):
            if 名.lower().endswith('.jsonl'):
                档.append(os.path.join(聊天归档目录, 名))
    except OSError:
        pass
    if os.path.isfile(聊记录文件):
        档.append(聊记录文件)
    return 档

def _聊天索引签名():
    文件们 = _聊天文件们()
    签 = []
    for 路 in 文件们:
        try:
            st = os.stat(路)
            签.append((路, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return (tuple(签), 文件们)

def _尝试初始化聊天总数(快照, 签):
    global _聊天总数, _聊天计数签名
    if _聊天总数 is not None:
        return
    with _聊天计数锁:
        if _聊天总数 is not None:
            return
        with _记录锁:
            try:
                签后, _ = _聊天索引签名()
            except Exception:
                return
            if 签 != 签后:
                return
            _聊天总数 = len(快照)
            _聊天计数签名 = 签后

def _聊天索引(严格=False):
    try:
        签, 文件们 = _聊天索引签名()
        with _聊天索引锁:
            if _聊天索引缓存['签名'] == 签:
                return _聊天索引缓存['条']
            旧快照 = _聊天索引缓存['条']
    except Exception:
        if 严格:
            return None
        with _聊天索引锁:
            return _聊天索引缓存['条']
    最后快照 = None
    for _ in range(4):
        try:
            签, 文件们 = _聊天索引签名()
            with _聊天索引锁:
                if _聊天索引缓存['签名'] == 签:
                    return _聊天索引缓存['条']
            条们 = []
            for 路 in 文件们:
                try:
                    with _聊天文件锁:
                        开头 = len(条们)
                        with open(路, 'r', encoding='utf-8') as f:
                            for 行 in f:
                                行 = 行.strip()
                                if not 行:
                                    continue
                                try:
                                    条 = json.loads(行)
                                except Exception:
                                    continue
                                if not isinstance(条, dict):
                                    continue
                                条 = dict(条)
                                条['序'] = len(条们)
                                条们.append(条)
                        if len(条们) > 开头 and 路 != 聊记录文件:
                            条们[-1]['本地分段'] = os.path.basename(路)
                except (OSError, UnicodeError) as e:
                    print('[记录] 这个文件读不动，本轮作废：%s（%s）' % (os.path.basename(路), e), flush=True)
                    条们 = None
                    break
            if 条们 is None:
                continue
            最后快照 = tuple(条们)
            签后, 文件们后 = _聊天索引签名()
            if 签 != 签后 or tuple(文件们) != tuple(文件们后):
                continue
            with _聊天索引锁:
                if _聊天索引缓存['签名'] == 签:
                    return _聊天索引缓存['条']
                _聊天索引缓存['签名'] = 签
                _聊天索引缓存['条'] = 最后快照
            _尝试初始化聊天总数(最后快照, 签)
            return 最后快照
        except Exception:
            continue
    with _聊天索引锁:
        if _聊天索引缓存['条']:
            return None if 严格 else _聊天索引缓存['条']
    if 严格:
        return None
    return 最后快照 if 最后快照 is not None else 旧快照

def _复制历史条(原):
    条 = dict(原 or {})
    if globals().get('_可靠索引'):
        条 = _可靠索引.overlay(条)
    段出 = []
    for 原段 in 条.get('段') or []:
        if not isinstance(原段, dict):
            continue
        段 = dict(原段)
        media_id = str(段.get('媒体ID') or '').strip()
        if media_id and 媒体库:
            item = 媒体库.get_media(media_id)
            if item:
                段['url'] = _媒体段地址(段)
                段['小图'] = _媒体段地址(段, True)
                段['保留'] = '永久' if item.get('retention') == 'permanent' else '临时'
                段['保留状态'] = '回收站' if item.get('status') == 'trash' else '永久' if item.get('retention') == 'permanent' else '临时'
                if item.get('status') in ('trash', 'deleted'):
                    段['已过期'] = True
                    段['过期提示'] = '这张临时媒体已过期，缩略图仍保留；可在存储管理里恢复或永久保留' if item.get('status') == 'trash' else '这张临时媒体的清晰版本已清理，消息和缩略图仍保留'
            else:
                段['缺失'] = True
        else:
            名 = os.path.basename(str(段.get('文件') or ''))
            if 名:
                段['url'] = _媒体段地址(段)
                段['小图'] = _媒体段地址(段, True)
        段出.append(段)
    条['段'] = 段出
    条['segments'] = 段出
    return 条

def _确保聊天总数():
    global _聊天总数, _聊天计数签名
    with _聊天计数锁:
        try:
            当前签名, _ = _聊天索引签名()
        except Exception:
            当前签名 = None
        if _聊天总数 is not None and 当前签名 is not None and (当前签名 == _聊天计数签名):
            return _聊天总数
        if _聊天归档中 and _聊天总数 is not None:
            return _聊天总数
        快照 = None
        for _ in range(8):
            快照 = _聊天索引(严格=True)
            if 快照 is None:
                continue
            try:
                签, 文件们 = _聊天索引签名()
            except Exception:
                continue
            with _记录锁:
                try:
                    签后, 文件们后 = _聊天索引签名()
                except Exception:
                    continue
                if 签 == 签后 and tuple(文件们) == tuple(文件们后):
                    _聊天总数 = len(快照)
                    _聊天计数签名 = 签后
                    return _聊天总数
        _聊天总数 = None
        _聊天计数签名 = None
        return None

def _总行数():
    return len(_聊天索引())

def _历史页(条数=150, 跳过=0, 上界=None):
    try:
        条数 = max(0, int(条数))
    except Exception:
        条数 = 150
    try:
        跳过 = max(0, int(跳过))
    except Exception:
        跳过 = 0
    try:
        上 = None if 上界 in (None, '') else max(0, int(上界))
    except Exception:
        上 = None
    try:
        全 = _聊天索引()
        总 = len(全)
        if 上 is None:
            尾 = max(0, 总 - 跳过)
        else:
            尾 = min(总, 上)
        头 = max(0, 尾 - 条数)
        return {'消息': [_复制历史条(条) for 条 in 全[头:尾]], '总': 总, '上界': 尾, '前游标': 头, '还有更早': bool(头 > 0)}
    except Exception:
        return {'消息': [], '总': 0, '上界': 0, '前游标': 0, '还有更早': False}

def 读历史(条数=150, 跳过=0, 上界=None):
    return _历史页(条数, 跳过, 上界)['消息']

class Api(PanelRuntime):

    def __init__(self, config=None):
        super().__init__(globals(), config or CONFIG)
        self.diary = Diary(self.storage["DATA_DIR"])
        self.mobile = MobileChat(self)
        from test_room import TestRoom
        self._test_room = TestRoom() if self.config.get("_test_room_enabled") is True else TestRoom({})

    def close(self):
        self.mobile.close()
        super().close()

    def mobile_view(self):
        return self.mobile.view()

    def mobile_manage(self, action, port=None):
        return self.mobile.manage(action, port)

    def test_window(self, method, args):
        return self._test_room.call(method, args)

    def diary_dates(self):
        return self.diary.dates()

    def diary_read(self, day):
        return self.diary.read(day)

    def diary_image(self, day, name):
        return self.diary.image(day, name)

    def diary_add_photo(self, day, encoded):
        return self.diary.add_photo(day, encoded)

    def diary_save(self, day, body, revision):
        return self.diary.save(day, body, revision)

    def _往外说(self, 谁, 段, 文='', 来自='', 瘦=None, 记录序=None,
               message_id='', event_id='', delivery_status='', endpoint_id='', message=None):
        global _来信序
        显示段 = _复制历史条({'段': 瘦 if 瘦 is not None else 段}).get('段') or []
        with _来信锁:
            _来信序 += 1
            _来信.append({**(message or {}), '序': _来信序, '谁': 谁, '文': 文, '段': 显示段, '段瘦': 显示段,
                          '记录序': 记录序, '来自': 来自 or '',
                          '时': (message or {}).get('created_at') or time.time(),
                          'message_id': str(message_id or ''), 'event_id': str(event_id or ''),
                          'delivery_status': str(delivery_status or ''),
                          'endpoint_id': str(endpoint_id or '')})
            return _来信序

    def sticker_list(self):
        return {'ok': True, '表情包': _列图(表情包目录), '食物': _列图(食物目录)}

    def sticker_add(self, 类别, 名, b64, 覆盖=False):
        目录 = _图目录(类别)
        if not 目录:
            return {'ok': False, 'error': '不认识的类别'}
        b64 = str(b64 or '')
        if b64.startswith('data:'):
            b64 = b64.split(',', 1)[-1]
        try:
            data = base64.b64decode(b64)
        except Exception:
            return {'ok': False, 'error': '图读不出来'}
        if not data:
            return {'ok': False, 'error': '图是空的'}
        原名 = os.path.basename(str(名 or '新图.png')).strip() or '新图.png'
        if not 原名.lower().endswith(图后缀们):
            原名 += '.png'
        底, 后缀 = os.path.splitext(原名)
        try:
            os.makedirs(目录, exist_ok=True)
            全 = os.path.join(目录, 原名)
            if 覆盖 and os.path.isfile(全):
                _放手删(全)
                os.remove(全)
            n = 2
            while os.path.exists(全):
                全 = os.path.join(目录, '%s-%d%s' % (底, n, 后缀))
                n += 1
            with open(全, 'wb') as f:
                f.write(data)
        except Exception as e:
            return {'ok': False, 'error': '写不进去：%s' % e}
        return {'ok': True, '名': os.path.basename(全), '列表': _列图(目录)}

    def sticker_del(self, 类别, 名):
        目录 = _图目录(类别)
        if not 目录:
            return {'ok': False, 'error': '不认识的类别'}
        名 = os.path.basename(str(名 or ''))
        if not 名:
            return {'ok': False, 'error': '没说是哪一张'}
        全 = os.path.join(目录, 名)
        if not os.path.isfile(全):
            return {'ok': False, 'error': '这张已经不在了'}
        try:
            _放手删(全)
            os.remove(全)
        except Exception as e:
            return {'ok': False, 'error': '删不掉：%s' % e}
        return {'ok': True, '列表': _列图(目录)}

    def sticker_send(self, 类别, 名, 文='', 来自=''):
        目录 = _图目录(类别)
        if not 目录:
            return {'ok': False, 'error': '不认识的类别'}
        名 = os.path.basename(str(名 or ''))
        全 = os.path.join(目录, 名)
        if not os.path.isfile(全):
            return {'ok': False, 'error': '这张图不见了'}
        try:
            with open(全, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
        except Exception as e:
            return {'ok': False, 'error': '读不出来：%s' % e}
        return self.chat_send_image(b64, 名, 文, 来自)

    def gallery_list(self, 起=0, 数=9, 排序='新', 搜索='', 月份='', 游标=None, 收纳=False, 版本=''):
        try:
            起 = int(起)
        except Exception:
            起 = 0
        try:
            数 = int(数)
        except Exception:
            数 = 9
        数 = max(1, min(60, 数))
        排序 = '旧' if str(排序) in ('旧', 'old', 'asc') else '新'
        try:
            if 媒体库:
                items, all_version = _媒体画册项(收纳, 搜索, 月份, 排序)
                月计 = {}
                for item in _媒体画册项(收纳, 搜索, '', 排序)[0]:
                    月键 = time.strftime('%Y-%m', time.localtime(float(item.get('created_at') or 0)))
                    月计[月键] = 月计.get(月键, 0) + 1
                条件 = _画册条件签(排序, 搜索, 月份, 收纳)
                基线 = str(版本 or all_version)
                remaining = items
                if 游标 is not None:
                    if not isinstance(游标, dict) or 游标.get('条件') != 条件:
                        raise ValueError('浏览条件已经改变，请刷新画册')
                    try:
                        key = (float(游标['时']), str(游标['媒体ID']))
                        if not math.isfinite(key[0]) or not key[1].startswith('m_'):
                            raise ValueError()
                        基线 = str(游标['版本'])
                    except (KeyError, TypeError, ValueError):
                        raise ValueError('续取位置无效，请刷新画册') from None
                    remaining = [item for item in items if ((float(item['created_at']), item['media_id']) < key if 排序 == '新' else (float(item['created_at']), item['media_id']) > key)]
                    start = 0
                else:
                    start = max(0, int(起))
                page_size = max(1, int(数))
                页 = remaining[start:start + page_size]
                画 = []
                for item in 页:
                    name = str(item.get('display_name') or item['media_id'])
                    画.append({'名': item['media_id'], '媒体ID': item['media_id'], '原名': name, '时': float(item.get('created_at') or 0), '字节': int(item.get('archive_bytes') or 0), '宽': int(item.get('width') or 0), '高': int(item.get('height') or 0), '版本': item['_版本'], '收纳': bool(收纳), '小图': _媒体段地址({'媒体ID': item['media_id']}, True), '原图': _媒体段地址({'媒体ID': item['media_id']}, False), '保留状态': '永久' if item.get('retention') == 'permanent' else '临时'})
                还有 = start + len(页) < len(remaining)
                return {'ok': True, '画': 画, '总': len(items), '全册总': len(items), '月': 月计, '最早': min((float(x.get('created_at') or 0) for x in items), default=0), '最新': max((float(x.get('created_at') or 0) for x in items), default=0), '版本': all_version, '变化': 基线 != all_version, '浏览状态': '版本已变化' if 基线 != all_version else '当前版本', '下个': {'时': 页[-1]['created_at'], '媒体ID': 页[-1]['media_id'], '条件': 条件, '版本': 基线} if 还有 and 页 else None, '还有': 还有, '起': start, '数': page_size, '排序': 排序}
            包 = _列画册(起, 数, 排序, 搜索, 月份, 游标, bool(收纳), 版本)
            return {'ok': True, **{k: v for k, v in 包.items() if k != '页'}, '画': 包['页'], '起': max(0, 起), '数': 数, '排序': 排序}
        except Exception as e:
            return {'ok': False, 'error': '画册暂时读不出来：%s' % e}

    def gallery_del(self, 名):
        if 媒体库 and str(名 or '').startswith('m_'):
            try:
                item = 媒体库.get_media(str(名))
                if not item:
                    return {'ok': False, 'error': '这张作品不存在'}
                if not 媒体库.update_collection(str(名), 'trash', kind='workshop'):
                    return {'ok': False, 'error': '这张作品不在画册里，请刷新'}
                return {'ok': True, '收纳名': str(名), '总': len(媒体库.list_workshop('gallery'))}
            except Exception as exc:
                return {'ok': False, 'error': '没能移入收纳箱：%s' % exc}
        global _画册改次
        try:
            名 = _画册安全名(名)
            os.makedirs(画册收纳目录, exist_ok=True)
            with _画册锁:
                全 = os.path.join(画册目录, 名)
                if not os.path.isfile(全) or os.path.islink(全):
                    return {'ok': False, 'error': '这张已经不在画册里了，请刷新'}
                收纳名 = '%013d_%s__%s' % (int(time.time() * 1000), uuid.uuid4().hex[:12], 名)
                os.rename(全, os.path.join(画册收纳目录, 收纳名))
                _画册改次 += 1
        except Exception as e:
            return {'ok': False, 'error': '没能移入收纳箱：%s' % e}
        return {'ok': True, '收纳名': 收纳名, '总': len(_扫画册()[0])}

    def gallery_restore(self, 名):
        if 媒体库 and str(名 or '').startswith('m_'):
            try:
                item = 媒体库.get_media(str(名))
                if not item:
                    return {'ok': False, 'error': '这张作品不存在'}
                if not 媒体库.update_collection(str(名), 'gallery', kind='workshop'):
                    return {'ok': False, 'error': '这张作品不在收纳箱里，请刷新'}
                return {'ok': True, '名': str(名), '总': len(媒体库.list_workshop('gallery'))}
            except Exception as exc:
                return {'ok': False, 'error': '没能恢复：%s' % exc}
        global _画册改次
        try:
            名 = _画册安全名(名)
            原名 = _画册原名(名, True)
            if 原名 == 名:
                return {'ok': False, 'error': '收纳记录无效'}
            _画册安全名(原名)
            os.makedirs(画册目录, exist_ok=True)
            with _画册锁:
                全 = os.path.join(画册收纳目录, 名)
                if not os.path.isfile(全) or os.path.islink(全):
                    return {'ok': False, 'error': '这张已经恢复过了，请刷新'}
                出 = _画册空名字(画册目录, 原名)
                os.rename(全, os.path.join(画册目录, 出))
                _画册改次 += 1
            return {'ok': True, '名': 出, '总': len(_扫画册()[0])}
        except Exception as e:
            return {'ok': False, 'error': '没能恢复：%s' % e}

    def gallery_status(self, 收纳=False, 搜索='', 月份='', 排序='新', 版本=''):
        try:
            if 媒体库:
                items, version = _媒体画册项(收纳, 搜索, 月份, 排序)
                return {'ok': True, '总': len(items), '版本': version, '变化': bool(版本 and str(版本) != version)}
            包 = _列画册(0, 0, 排序, 搜索, 月份, None, bool(收纳), str(版本 or ''))
            return {'ok': True, '总': 包['总'], '版本': 包['版本'], '变化': bool(版本 and str(版本) != 包['版本'])}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def gallery_export(self, 名, 收纳=False):
        try:
            if 媒体库 and str(名 or '').startswith('m_'):
                source = 媒体库.resolve_path(str(名), thumbnail=False)
                item = 媒体库.get_media(str(名))
                if not source or not item:
                    return {'ok': False, 'error': '原图已经被清理，请查看过期提示'}
                if not webview.windows:
                    return {'ok': False, 'error': '请在聊天面板窗口里保存原图'}
                原名 = str(item.get('display_name') or str(名))
                主体 = os.path.splitext(os.path.basename(原名))[0] or str(名)
                save_name = 主体 + _归档实际后缀(source, item)
                choices = webview.windows[0].create_file_dialog(webview.FileDialog.SAVE, save_filename=save_name, file_types=('图片文件 (*.png;*.jpg;*.jpeg;*.gif;*.webp)', '所有文件 (*.*)'))
                if not choices:
                    return {'ok': True, '取消': True}
                target = choices[0]
                if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(source)):
                    shutil.copy2(source, target)
                return {'ok': True}
            名 = _画册安全名(名)
            源 = os.path.join(画册收纳目录 if 收纳 else 画册目录, 名)
            if not os.path.isfile(源) or os.path.islink(源):
                return {'ok': False, 'error': '原图已经被移动，请刷新画册'}
            if not webview.windows:
                return {'ok': False, 'error': '请在聊天面板窗口里保存原图'}
            选择 = webview.windows[0].create_file_dialog(webview.FileDialog.SAVE, save_filename=_画册原名(名, bool(收纳)), file_types=('图片文件 (*.png;*.jpg;*.jpeg;*.gif;*.webp)', '所有文件 (*.*)'))
            if not 选择:
                return {'ok': True, '取消': True}
            目标 = 选择[0]
            if os.path.normcase(os.path.abspath(目标)) != os.path.normcase(os.path.abspath(源)):
                shutil.copy2(源, 目标)
            return {'ok': True}
        except Exception as e:
            return {'ok': False, 'error': '原图没能保存：%s' % e}

    def gallery_add(self, 名, b64):
        b64 = str(b64 or '')
        if b64.startswith('data:'):
            b64 = b64.split(',', 1)[-1]
        if len(b64) > (100 * 1024 * 1024 + 2) // 3 * 4:
            return {'ok': False, 'error': '这张图超过 100 MB，请换一张'}
        try:
            data = base64.b64decode(b64, validate=True)
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                后缀 = {'PNG': '.png', 'JPEG': '.jpg', 'GIF': '.gif', 'WEBP': '.webp'}.get(im.format)
                if not 后缀:
                    return {'ok': False, 'error': '支持 PNG、JPG、GIF 和 WebP 图片'}
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
        except Exception:
            return {'ok': False, 'error': '这张图不完整或不是有效图片'}
        原名 = re.sub('[<>:"/\\\\|?*\\x00-\\x1f]', '_', os.path.basename(str(名 or '新作品')))
        底 = os.path.splitext(原名)[0].strip(' .')[:170] or '新作品'
        if re.fullmatch('(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', 底):
            底 = '_' + 底
        try:
            if 媒体库:
                item = 媒体库.ingest_bytes(data, filename=底 + 后缀, mime='image/' + ('jpeg' if 后缀 == '.jpg' else 后缀[1:]), source='workshop', retention='permanent', metadata={'source': 'user-gallery'})
                reference = 媒体库.get_or_create_workshop_reference(item['media_id'], display_name=底 + 后缀, metadata={'source': 'user-gallery'})
                return {'ok': True, '名': item['media_id'], '媒体ID': item['media_id'], '原名': str(item.get('display_name') or 底 + 后缀), '重复': not reference['created'], '收纳': False, '总': len(媒体库.list_workshop('gallery'))}
            果 = _存入画册(底 + 后缀, data=data)
            if not 果['重复']:
                _缩略图(果['名'])
            return {'ok': True, **果, '总': len(_扫画册()[0])}
        except Exception as e:
            return {'ok': False, 'error': '原图没能保存：%s' % e}

    def gallery_collect(self):
        收 = 0
        try:
            for 条 in _聊天索引():
                if 条.get('谁') != '她':
                    continue
                for 段 in 条.get('段') or []:
                    if not isinstance(段, dict) or 段.get('type') != 'image':
                        continue
                    文 = os.path.basename(str(段.get('文件') or ''))
                    果 = _收画册(os.path.join(聊媒体目录, 文) if 文 else '', 详情=True, 媒体ID=str(段.get('媒体ID') or ''), 强制=True)
                    if 果 and (not 果['重复']):
                        收 += 1
        except Exception as e:
            return {'ok': False, 'error': '翻不动记录：%s' % e}
        总 = len(媒体库.list_workshop('gallery')) if 媒体库 else len(_扫画册()[0])
        return {'ok': True, '收了': 收, '总': 总}

    def media_settings(self):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        return {'ok': True, '设置': 媒体库.get_settings(), '统计': 媒体库.stats(), '迁移': dict(_媒体迁移状态)}

    def media_update_settings(self, values):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        try:
            before = 媒体库.get_settings()
            after = 媒体库.update_settings(values if isinstance(values, dict) else {})
            return {'ok': True, '设置': after, '统计': 媒体库.stats(), '影响': {'temporary_days_before': before.get('temporary_days'), 'temporary_days_after': after.get('temporary_days'), 'notice': '缩短期限只影响尚未到期的临时引用；已有文件不会被立即彻底删除，仍按回收站宽限期处理。' if int(after.get('temporary_days', 30)) < int(before.get('temporary_days', 30)) else '已有引用按创建时的到期时间计算，新引用使用新的期限。'}}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def media_maintenance(self):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        try:
            result = 媒体库.run_maintenance()
            result['迁移清理'] = 媒体库.cleanup_migration()
            result['迁移清理错误'] = 媒体库.migration_cleanup_errors()
            result['ok'] = True
            result['统计'] = 媒体库.stats()
            return result
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def media_trash(self):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        return {'ok': True, '媒体': 媒体库.list_trash(), '统计': 媒体库.stats()}

    def media_restore(self, media_id, permanent=False):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        try:
            item = 媒体库.restore(str(media_id), permanent=bool(permanent))
            return {'ok': True, '媒体': item, '统计': 媒体库.stats()}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def media_set_permanent(self, media_id, permanent=True):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        try:
            item = 媒体库.set_permanent(str(media_id), bool(permanent))
            return {'ok': True, '媒体': item, '统计': 媒体库.stats()}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def media_delete(self, media_id):
        if not 媒体库:
            return {'ok': False, 'error': '媒体归档不可用'}
        try:
            item = 媒体库.delete_now(str(media_id))
            return {'ok': True, '媒体': item, '统计': 媒体库.stats()}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    def song_import(self, name, encoded):
        return import_song(歌目录, name, encoded)

    def song_list(self):
        元 = {}
        try:
            with open(歌单文件, 'r', encoding='utf-8-sig') as f:
                元 = json.load(f) or {}
        except Exception:
            元 = {}
        表 = 元.get('歌') if isinstance(元, dict) else None
        表 = [x for x in 表 or [] if isinstance(x, dict) and x.get('文件')]
        说明 = {}
        for i, x in enumerate(表):
            说明[os.path.basename(str(x['文件']))] = (i, x)
        歌们 = []
        try:
            名们 = sorted(os.listdir(歌目录))
        except OSError:
            名们 = []
        for 名 in 名们:
            if not 名.lower().endswith(歌后缀们):
                continue
            全 = os.path.join(歌目录, 名)
            if not os.path.isfile(全):
                continue
            位, 条 = 说明.get(名, (999, {}))
            try:
                大小 = os.path.getsize(全)
                得 = os.path.getmtime(全)
            except OSError:
                大小, 得 = (0, 0)
            歌们.append({'文件': 名, '名': str(条.get('名') or os.path.splitext(名)[0]), '唱': str(条.get('唱') or '聊天助手'), '注': str(条.get('注') or ''), '标签': str(条.get('标签') or ''), '日': str(条.get('日') or ''), '制作': [str(x) for x in 条.get('制作') or [] if str(x).strip()][:12], '偏移': 条.get('偏移') or 0, '词': _读歌词(条, 名), '大小': 大小, '得': 得, '位': 位, 'url': 'assets/歌/' + urllib.parse.quote(名)})
        歌们.sort(key=lambda x: (x['位'], -x['得']))
        for x in 歌们:
            x.pop('位', None)
        cover = Path(歌目录) / '封面.png'
        return with_imports(歌目录, {
            'ok': True, '歌': 歌们,
            '封面': 'assets/歌/' + urllib.parse.quote('封面.png') if cover.is_file() else 'assets/default-peer.png',
            '在': 'data-dir/songs',
        })

    def chat_history(self, 条数=150, 跳过=0, 上界=None):
        try:
            条数 = int(条数 or 150)
        except Exception:
            条数 = 150
        try:
            跳过 = int(跳过 or 0)
        except Exception:
            跳过 = 0
        页 = _历史页(条数, 跳过, 上界)
        return {'ok': True, **页}

    def chat_poll(self, since=0):
        try:
            since = int(since or 0)
        except Exception:
            since = 0
        with _来信锁:
            items = [x for x in _来信 if x['序'] > since]
        safe = []
        for x in items:
            item = dict(x)
            item['段'] = _复制历史条({'段': x.get('段瘦') or x.get('段') or []}).get('段') or []
            item['段瘦'] = item['段']
            safe.append(item)
        return {'ok': True, '来信': safe}

    def chat_search(self, 词='', 类型='全部', 数=60, 跳过=0):
        词 = str(词 or '').strip()
        类 = str(类型 or '全部')
        try:
            数 = max(1, min(300, int(数 or 60)))
        except Exception:
            数 = 60
        try:
            跳 = max(0, int(跳过 or 0))
        except Exception:
            跳 = 0
        小 = 词.casefold()
        命中 = []
        for 条 in reversed(_聊天索引()):
            段们 = [x for x in 条.get('段') or [] if isinstance(x, dict)]
            文 = ' '.join((str(x.get('text') or '') for x in 段们 if x.get('type') == 'text')).strip()
            有图 = any((x.get('type') == 'image' for x in 段们))
            有表情 = any((x.get('type') == 'face' for x in 段们))
            有语音 = any((x.get('type') == 'record' for x in 段们))
            有文件 = any((x.get('type') in ('file', 'video') for x in 段们))
            if 类 in ('图片', '图像') and (not 有图):
                continue
            if 类 in ('语音', '音频') and (not 有语音):
                continue
            if 类 == '表情' and (not 有表情):
                continue
            if 类 == '文字' and (not 文):
                continue
            标签 = []
            if 有图:
                标签.append('[图片]')
            if 有表情:
                标签.append('[表情]')
            if 有语音:
                标签.append('[语音]')
            if 有文件:
                标签.append('[文件]')
            可搜 = (文 + ' ' + ' '.join(标签)).casefold()
            if 小 and 小 not in 可搜:
                continue
            命中.append({'谁': 条.get('谁') or '', '时': 条.get('时') or 0, '文': 文[:600], '图': 有图, '表情': 有表情, '语音': 有语音, '文件': 有文件, '序': 条.get('序', 0)})
        总 = len(命中)
        return {'ok': True, '条': 命中[跳:跳 + 数], '总': 总, '还有': 跳 + 数 < 总}

    def get_skin(self):
        try:
            with open(SKIN_FILE, encoding='utf-8') as f:
                return {'ok': True, 'skin': f.read().strip()[:20]}
        except Exception:
            return {'ok': True, 'skin': ''}

    def set_skin(self, name):
        try:
            with open(SKIN_FILE, 'w', encoding='utf-8') as f:
                f.write(str(name)[:20])
        except Exception:
            pass
        return {'ok': True}


def 起界面服务(api=None, port=None):
    global 界面端口, _server
    from http_app import create_server
    初始化媒体库()
    api = api or Api(CONFIG)
    _server = create_server(globals(), api, port)
    api.start()
    界面端口 = _server.server_address[1]
    threading.Thread(target=_server.serve_forever, daemon=True).start()
    return 界面端口


def main():
    import argparse
    import sys
    import webbrowser
    from settings import load_config
    parser = argparse.ArgumentParser(description="鱼鱼小窝")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--test-window", action="store_true", help="打开显式配置的隔离文字测试窗口")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config, args.data_dir, args.demo, args.port)
    config["_test_room_enabled"] = args.test_window
    if args.test_window:
        from test_room import TestRoom
        if not TestRoom().enabled():
            parser.error("尚未配置独立测试窗口")
    configure_data(config["data_dir"], config)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from starter_assets import seed_food
    seed_food(DATA_DIR, Path(WEB_DIR))
    初始化媒体库()
    api = Api(config)
    if config["mode"] == "demo" and not config.get("_console_managed"):
        from demo import seed
        seed(globals(), api)
    port = 起界面服务(api, config["port"])
    address = "http://127.0.0.1:%d/index.html" % port
    if args.test_window:
        address += "?chat=test"
    print(json.dumps({"url": address, "mode": config["mode"],
                      "data_dir": str(DATA_DIR)}, ensure_ascii=False), flush=True)
    if args.smoke_test:
        api.close()
        _server.shutdown()
        _server.server_close()
        print("SMOKE_TEST=PASS", flush=True)
        return 0
    threading.Thread(target=_媒体后台, daemon=True, name="media-maintenance").start()
    try:
        if args.desktop:
            global webview
            from desktop_window import DESKTOP_UNAVAILABLE, run_desktop
            from http_app import RPC_METHODS
            try:
                import webview
                run_desktop(webview, api, RPC_METHODS, config["title"], address, WEB_DIR, WIN)
            except ImportError:
                print("桌面模式需要安装 panel/requirements-desktop.txt", file=sys.stderr)
                return DESKTOP_UNAVAILABLE
            except Exception as exc:
                print(f"桌面窗口启动失败：{exc}", file=sys.stderr)
                return DESKTOP_UNAVAILABLE
        else:
            if not args.no_browser:
                webbrowser.open(address)
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        api.close()
        _server.shutdown()
        _server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
