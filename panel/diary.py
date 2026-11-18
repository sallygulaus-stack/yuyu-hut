"""User-owned diary files; no connector calls or arbitrary pathname reads."""

import base64
from contextlib import closing
from datetime import date
import hashlib
import io
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import threading

from PIL import Image


MAX_TEXT = 20000
MAX_PHOTO = 10 * 1024 * 1024
PHOTO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}


def valid_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def ordinary(path, is_type):
    try:
        s = path.lstat()
    except FileNotFoundError:
        return None
    if not is_type(s.st_mode) or getattr(s, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("日记路径不是普通文件或目录")
    return s


def read_bytes(path, limit):
    before = ordinary(path, stat.S_ISREG)
    if before is None:
        return None
    # Verify the opened handle before reading, including on platforms without O_NOFOLLOW.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        after = ordinary(path, stat.S_ISREG)
        if not after or not os.path.samestat(before, os.fstat(handle.fileno())) or not os.path.samestat(before, after):
            raise ValueError("日记文件在读取时已改变")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("日记文件超出大小限制")
    return raw


def photo_day(name):
    if (not isinstance(name, str) or len(name) > 240 or any(c in name for c in "/\\:\0") or
            not valid_day(name[:10]) or name[10:11] != "-" or
            name[11:].startswith(".") or Path(name).suffix.lower() not in PHOTO_TYPES):
        return ""
    return name[:10]


class Diary:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / "diary"
        self.photos = self.root / "photos"
        self.legacy = self.root / "entries.sqlite3"
        self.lock = threading.RLock()

    def folder(self):
        if ordinary(self.root, stat.S_ISDIR) is None:
            self.root.mkdir(parents=True, exist_ok=True)
            ordinary(self.root, stat.S_ISDIR)
        if ordinary(self.photos, stat.S_ISDIR) is None:
            self.photos.mkdir(exist_ok=True)
            ordinary(self.photos, stat.S_ISDIR)

    def old_rows(self, day=None):
        if not ordinary(self.legacy, stat.S_ISREG):
            return []
        uri = self.legacy.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            if day is not None:
                return db.execute("SELECT body, revision FROM entries WHERE day=?", (day,)).fetchall()
            return db.execute("SELECT day FROM entries").fetchall()

    def content(self, day):
        raw = read_bytes(self.root / (day + ".md"), MAX_TEXT * 4 + 3)
        if raw is not None:
            return raw.decode("utf-8-sig"), hashlib.sha256(b"md\0" + raw).hexdigest()
        rows = self.old_rows(day)
        if rows:
            body, revision = rows[0]
            digest = hashlib.sha256((f"old:{revision}\0" + body).encode("utf-8")).hexdigest()
            return body, digest
        return "", 0

    def photo_list(self, day):
        photos = []
        for path in sorted(self.photos.iterdir()):
            if photo_day(path.name) != day:
                continue
            try:
                s = ordinary(path, stat.S_ISREG)
            except ValueError:
                continue
            if s and 0 < s.st_size <= MAX_PHOTO:
                photos.append({"name": path.name, "stamp": f"{s.st_mtime_ns}:{s.st_size}"})
            if len(photos) >= 24:
                break
        return photos

    def dates(self):
        try:
            self.folder()
            days = {day for (day,) in self.old_rows() if valid_day(day)}
            for directory in (self.root, self.photos):
                for path in directory.iterdir():
                    day = (path.stem if path.suffix == ".md" else "") if directory == self.root else photo_day(path.name)
                    if not valid_day(day):
                        continue
                    try:
                        if ordinary(path, stat.S_ISREG):
                            days.add(day)
                    except ValueError:
                        continue
            return {"ok": True, "dates": sorted(days, reverse=True)}
        except (ValueError, OSError, sqlite3.Error):
            return {"ok": False, "error": "日记目录暂时读不到"}

    def read(self, day):
        if not valid_day(day):
            return {"ok": False, "error": "日期无效"}
        try:
            self.folder()
            body, revision = self.content(day)
            return {"ok": True, "day": day, "body": body, "revision": revision,
                    "photos": self.photo_list(day)}
        except (ValueError, OSError, sqlite3.Error):
            return {"ok": False, "error": "这一天的日记读不到，请确认正文是 UTF-8 文本"}

    def image(self, day, name):
        if not valid_day(day) or photo_day(name) != day:
            return {"ok": False, "error": "日记配图名无效"}
        try:
            self.folder()
            raw = read_bytes(self.photos / name, MAX_PHOTO)
            if raw:
                mime = PHOTO_TYPES[Path(name).suffix.lower()]
                with Image.open(io.BytesIO(raw)) as image:
                    if Image.MIME.get(image.format) != mime or image.width * image.height > 40_000_000:
                        raise ValueError("图片格式或尺寸无效")
                    image.verify()
                return {"ok": True, "image": "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")}
        except (ValueError, OSError, Image.DecompressionBombError):
            pass
        return {"ok": False, "error": "日记配图暂时读不到"}

    def add_photo(self, day, encoded):
        if not valid_day(day) or not isinstance(encoded, str) or len(encoded) > (MAX_PHOTO + 2) // 3 * 4:
            return {"ok": False, "error": "请选择 10 MiB 以内的日记配图"}
        try:
            raw = base64.b64decode(encoded, validate=True)
            if not 0 < len(raw) <= MAX_PHOTO:
                raise ValueError()
            with Image.open(io.BytesIO(raw)) as image:
                ext = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp"}[image.format]
                if image.width * image.height > 40_000_000:
                    raise ValueError()
                image.verify()
        except (KeyError, ValueError, OSError, Image.DecompressionBombError, SyntaxError):
            return {"ok": False, "error": "请选择有效的 PNG、JPEG、GIF 或 WebP 图片"}
        name = day + "-" + hashlib.sha256(raw).hexdigest()[:24] + ext
        with self.lock:
            try:
                self.folder()
                target = self.photos / name
                if ordinary(target, stat.S_ISREG):
                    if read_bytes(target, MAX_PHOTO) == raw:
                        return {"ok": True, "duplicate": True}
                    return {"ok": False, "error": "同名配图已在外部修改，请保留原图"}
                if len(self.photo_list(day)) >= 24:
                    return {"ok": False, "error": "这一天已有 24 张配图"}
                temp = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.photos, suffix=".part", delete=False) as handle:
                        temp = Path(handle.name)
                        handle.write(raw)
                    if os.name == "nt":
                        os.rename(temp, target)
                    else:
                        os.link(temp, target)
                except FileExistsError:
                    return {"ok": read_bytes(target, MAX_PHOTO) == raw, "duplicate": True}
                finally:
                    if temp:
                        temp.unlink(missing_ok=True)
                return {"ok": True}
            except (ValueError, OSError):
                return {"ok": False, "error": "配图未能存入日记目录"}

    def save(self, day, body, revision):
        if not valid_day(day) or not (type(revision) is int and revision == 0 or
                                      isinstance(revision, str) and re.fullmatch("[0-9a-f]{64}", revision)):
            return {"ok": False, "error": "日期或版本无效"}
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_TEXT or "\0" in body:
            return {"ok": False, "error": "请输入 1 到 20000 字的正文"}
        try:
            raw = body.encode("utf-8")
        except UnicodeError:
            return {"ok": False, "error": "正文包含无法保存的字符"}
        target = self.root / (day + ".md")
        conflict = {"ok": False, "conflict": True, "error": "这一天的正文已在别处修改，草稿仍保留。"}
        temp = None
        with self.lock:
            try:
                self.folder()
                if self.content(day)[1] != revision:
                    return conflict
                with tempfile.NamedTemporaryFile(dir=self.root, prefix="._diary-", suffix=".tmp", delete=False) as output:
                    temp = Path(output.name)
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                self.folder()
                if self.content(day)[1] != revision:
                    return conflict
                if ordinary(target, stat.S_ISREG):
                    os.replace(temp, target)
                elif os.name == "nt":
                    os.rename(temp, target)
                else:
                    os.link(temp, target)
                return {"ok": True, "day": day, "revision": hashlib.sha256(b"md\0" + raw).hexdigest()}
            except FileExistsError:
                return conflict
            except (ValueError, OSError, sqlite3.Error):
                return {"ok": False, "error": "日记未能写入数据目录，草稿仍保留。"}
            finally:
                try:
                    if temp and ordinary(self.root, stat.S_ISDIR):
                        temp.unlink(missing_ok=True)
                except (ValueError, OSError):
                    pass
