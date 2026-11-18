"""Local-only appearance overrides. Does not write connection or profile sources."""

import base64
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
import threading

from PIL import Image, ImageOps


ROLES = ("me", "peer")
TEXT_LIMITS = {"display_name": 40, "signature": 160}
MAX_UPLOAD = 10 * 1024 * 1024
MAX_APPEARANCE_BODY = (MAX_UPLOAD + 2) // 3 * 4 + 4096
MAX_STORED = 2 * 1024 * 1024
AVATAR_SIDE = 512
AVATAR_ROUTE = re.compile(r"/appearance/(me|peer)/([0-9a-f]{24})\.png")


def _decode(value, limit):
    if not isinstance(value, str) or len(value) > (limit + 2) // 3 * 4:
        raise ValueError("Invalid avatar size")
    payload = base64.b64decode(value, validate=True)
    if not payload or len(payload) > limit:
        raise ValueError("Invalid avatar size")
    return payload


def _avatar(value, stored=False):
    payload = _decode(value, MAX_STORED if stored else MAX_UPLOAD)
    formats = ("PNG",) if stored else ("PNG", "JPEG", "GIF", "WEBP", "BMP")
    with Image.open(BytesIO(payload), formats=formats) as image:
        if stored:
            if max(image.size) > AVATAR_SIDE:
                raise ValueError("Invalid stored avatar dimensions")
            image.verify()
            return payload
        if image.width * image.height > 32_000_000:
            raise ValueError("Avatar has too many pixels")
        resized = ImageOps.exif_transpose(image).convert("RGBA")
        resized.thumbnail((AVATAR_SIDE, AVATAR_SIDE), Image.Resampling.LANCZOS)
        clean = Image.new("RGBA", resized.size)
        clean.paste(resized)
        output = BytesIO()
        clean.save(output, "PNG")
        return output.getvalue()


def _avatar_id(payload):
    return sha256(payload).hexdigest()[:24]


class Appearance:
    def __init__(self, path, safe_text):
        self.path = Path(path)
        self.safe_text = safe_text
        self.lock = threading.Lock()
        self.entries = self._load()

    def _text(self, field, value):
        if not isinstance(value, str):
            raise ValueError("Invalid appearance text")
        value = value.strip()
        value.encode("utf-8")
        limit = TEXT_LIMITS[field]
        if len(value) > limit or any(ord(char) < 32 and char != "\n" for char in value):
            raise ValueError("Invalid appearance text")
        if field == "display_name" and (not value or "\n" in value):
            raise ValueError("A single-line nickname is required")
        if self.safe_text(value, limit) != value:
            raise ValueError("Private information in appearance")
        return value

    def _load(self):
        limit = 2 * ((MAX_STORED + 2) // 3 * 4 + 4096)
        try:
            with self.path.open("rb") as file:
                raw = file.read(limit + 1)
            data = json.loads(raw) if len(raw) <= limit else {}
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(data, dict):
            return {}
        if set(data) == TEXT_LIMITS.keys():
            # A previous trial version stored only the owner's two text fields.
            data = {"me": data}
        result = {}
        for role in ROLES:
            original = data.get(role)
            if not isinstance(original, dict):
                continue
            entry = {}
            for field, value in original.items():
                try:
                    if field in TEXT_LIMITS:
                        entry[field] = self._text(field, value)
                    elif field == "avatar":
                        entry[field] = _avatar(value, stored=True)
                except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
                    continue
            if entry:
                result[role] = entry
        return result

    def _save(self, entries):
        raw = {role: {field: base64.b64encode(value).decode("ascii") if field == "avatar" else value
                      for field, value in entry.items()} for role, entry in entries.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".appearance-", suffix=".tmp", delete=False) as file:
                temporary = file.name
                json.dump(raw, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def edit(self, role, patch):
        if role not in ROLES or not isinstance(patch, dict) or not patch:
            return {"ok": False, "error": "请选择要修改的资料。"}
        reset = set(patch) == {"reset"} and patch["reset"] is True
        if not reset and set(patch) - {*TEXT_LIMITS, "avatar"}:
            return {"ok": False, "error": "只能修改昵称、签名和头像。"}
        try:
            clean = {field: self._text(field, patch[field]) for field in TEXT_LIMITS if field in patch}
        except (TypeError, ValueError):
            return {"ok": False, "error": "请检查昵称和签名，别把私密信息写在这里。"}
        if "avatar" in patch:
            try:
                clean["avatar"] = _avatar(patch["avatar"])
            except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
                return {"ok": False, "error": "头像打不开，请选一张不超过 10 MB 的常见格式图片。"}
        with self.lock:
            entries = dict(self.entries)
            if reset:
                entries.pop(role, None)
            else:
                entries[role] = {**entries.get(role, {}), **clean}
            if entries != self.entries:
                try:
                    self._save(entries)
                except OSError:
                    return {"ok": False, "error": "这次没能保存，请再试一次。"}
                self.entries = entries
        return {"ok": True}

    def present(self, payload):
        entries = self.entries
        result = {**payload, "appearance_customized": {role: role in entries for role in ROLES}}
        for role in ROLES:
            entry = entries.get(role)
            if not entry:
                continue
            profile = {**payload[role], **{field: value for field, value in entry.items() if field != "avatar"}}
            if "avatar" in entry:
                profile["avatar"] = "/appearance/%s/%s.png" % (role, _avatar_id(entry["avatar"]))
            result[role] = profile
            result[role + "_name"] = profile["display_name"]
            if (payload.get("profile") or {}).get("profile_id") == role:
                result["profile"] = profile
        return result

    def serve_avatar(self, handler, route):
        found = AVATAR_ROUTE.fullmatch(route)
        if found:
            role, digest = found.groups()
            payload = self.entries.get(role, {}).get("avatar")
            if payload and digest == _avatar_id(payload):
                handler.send_response(200)
                handler.send_header("Content-Type", "image/png")
                handler.send_header("Content-Length", str(len(payload)))
                handler.send_header("X-Content-Type-Options", "nosniff")
                handler.send_header("Cache-Control", "no-store")
                handler.end_headers()
                try:
                    handler.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
        handler.send_error(404)
