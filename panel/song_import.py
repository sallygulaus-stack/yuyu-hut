"""Store owner-selected audio alongside, but separate from, curated tracks."""

import base64
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import quote


MAX_SONG = 32 * 1024 * 1024
MAX_SONG_BODY = (MAX_SONG + 2) // 3 * 4 + 4096
EXTENSIONS = frozenset((".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus"))
WINDOWS_DEVICES = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$) *(?:\.|$)", re.I)


def _song_name(name):
    if not isinstance(name, str) or len(name) > 256 or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name):
        raise ValueError("Invalid filename")
    stem, extension = os.path.splitext(unicodedata.normalize("NFC", name).strip())
    stem, extension = stem.strip(" .")[:58], extension.lower()
    if not stem or extension not in EXTENSIONS or WINDOWS_DEVICES.match(stem):
        raise ValueError("Invalid song name")
    stem.encode("utf-8")
    return stem, extension


def _audio_header(data, extension):
    if len(data) < 32:
        return False
    if extension == ".wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if extension == ".m4a":
        return data[4:8] == b"ftyp"
    if extension == ".flac":
        return data.startswith(b"fLaC")
    if extension in (".ogg", ".opus"):
        return data.startswith(b"OggS\x00") and (extension == ".ogg" or b"OpusHead" in data[27:512])
    start = 0
    if data.startswith(b"ID3"):
        if any(byte >= 128 for byte in data[6:10]):
            return False
        start = 10 + sum(byte << ((3 - index) * 7) for index, byte in enumerate(data[6:10]))
        if data[3] == 4 and data[5] & 0x10:
            start += 10
    for index in range(start, min(start + 2048, len(data) - 3)):
        b, c = data[index + 1:index + 3]
        if (data[index] == 255 and b & 0xe0 == 0xe0 and b & 0x18 != 0x08 and b & 0x06
                and c & 0xf0 not in (0, 0xf0) and c & 0x0c != 0x0c):
            return True
    return False


def _directory(library):
    directory = Path(library).resolve() / "imported"
    if directory.is_symlink() or directory.resolve() != directory:
        raise OSError("Imported playlist must stay in the songs directory")
    return directory


def import_song(library, name, encoded):
    try:
        stem, extension = _song_name(name)
    except ValueError:
        return {"ok": False, "error": "文件名或音频格式不支持。"}
    if not isinstance(encoded, str) or len(encoded) > (MAX_SONG + 2) // 3 * 4:
        return {"ok": False, "error": "单首歌曲不能超过 32 MB。"}
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        return {"ok": False, "error": "音频文件无法读取。"}
    if len(data) > MAX_SONG:
        return {"ok": False, "error": "单首歌曲不能超过 32 MB。"}
    if not _audio_header(data, extension):
        return {"ok": False, "error": "音频内容与文件格式不符。"}

    temp = None
    try:
        directory = _directory(library)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".song-", suffix=".tmp", delete=False) as file:
            temp = file.name
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        for number in range(1, 1000):
            suffix = "" if number == 1 else " (%d)" % number
            filename = stem + suffix + extension
            if (directory.parent / filename).exists():
                continue
            try:
                # Publish complete bytes without overwriting an import from another window.
                if os.name == "nt":
                    os.rename(temp, directory / filename)
                    temp = None
                else:
                    os.link(temp, directory / filename)
                return {"ok": True, "filename": filename}
            except FileExistsError:
                continue
    except OSError:
        return {"ok": False, "error": "歌曲未保存，请检查本地数据目录。"}
    finally:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass
    return {"ok": False, "error": "同名文件太多，请先换一个文件名。"}


def with_imports(library, playlist):
    imported = []
    try:
        for file in _directory(library).iterdir():
            try:
                _song_name(file.name)
                if not file.is_symlink() and file.is_file():
                    imported.append({"文件": file.name, "名": file.stem, "唱": "本机导入", "大小": file.stat().st_size,
                                     "制作": [], "词": "", "url": "assets/歌/imported/" + quote(file.name)})
            except (ValueError, OSError):
                continue
    except OSError:
        pass
    imported.sort(key=lambda song: song["文件"].casefold())
    return {**playlist, "歌": [*playlist.get("歌", []), *imported]}
