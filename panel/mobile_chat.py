"""An opt-in, independently paired loopback listener for chat only."""
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import quote, unquote, urlsplit

from appearance import AVATAR_ROUTE
from http_app import LocalFilesHandler
from media_limits import DEFAULT_LIMITS, encoded_size, media_limits


PAIR_SECONDS = 300
PAGE_SIZE = 30
ORIGIN = "mobile"
SHELL = {"/": ("mobile/index.html", "text/html; charset=utf-8"),
         "/mobile.css": ("mobile/mobile.css", "text/css; charset=utf-8"),
         "/mobile.js": ("mobile/mobile.js", "text/javascript; charset=utf-8"),
         "/me.png": ("assets/default-me.png", "image/png"),
         "/peer.png": ("assets/default-peer.png", "image/png")}
AUTH = re.compile(r"[A-Za-z0-9_-]{24,44}")
ARCHIVED_MEDIA = re.compile(r"m_[0-9a-f]{32}")
MIME = {
    "image": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp", ".bmp": "image/bmp", ".apng": "image/apng"},
    "record": {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".webm": "audio/webm",
               ".mp4": "audio/mp4", ".amr": "audio/amr"},
}


def in_loopback(host):
    if not isinstance(host, str):
        return False
    match = re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::([0-9]{1,5}))?", host)
    return bool(match and (match[1] is None or 0 < int(match[1]) < 65536))


def safe_name(name):
    return isinstance(name, str) and 0 < len(name) <= 240 and name not in {".", ".."} and not any(
        character in "/\\:" or ord(character) < 32 or "\ud800" <= character <= "\udfff" for character in name)


def seal(key, kind, name):
    return hmac.new(key, (kind + "\0" + name).encode("utf-8"), hashlib.sha256).hexdigest()


def attachment(key, segment):
    kind = segment.get("type")
    name = segment.get("媒体ID") or segment.get("文件")
    if kind not in MIME or not safe_name(name):
        return {"type": kind, "url": ""}
    return {"type": kind, "url": f"/media/{kind}/{quote(name, safe='')}/{seal(key, kind, name)}"}


def message(key, row):
    parts = []
    for segment in row.get("段") or []:
        kind = segment.get("type")
        if kind == "text":
            parts.append({"type": kind, "text": str(segment.get("text") or "")})
        elif kind in MIME:
            parts.append(attachment(key, segment))
        elif kind in ("face", "file", "video"):
            parts.append({"type": kind})
    return {"sequence": row["序"], "mine": row.get("谁") == "我", "time": row.get("时") or 0,
            "parts": parts, "split": bool(row.get("本地分段")), "delivery": row.get("delivery_status") or ""}


class Pairing:
    def __init__(self):
        self.lock = threading.Lock()
        self.code = self.token = self.key = None
        self.expires = 0
        self.renew()

    def renew(self):
        with self.lock:
            self.code, self.expires = secrets.token_urlsafe(18), time.monotonic() + PAIR_SECONDS
            self.token = self.key = None

    def revoke(self):
        with self.lock:
            self.code = self.token = self.key = None

    def view(self):
        with self.lock:
            left = max(0, int(self.expires - time.monotonic() + .999))
            return {"paired": self.token is not None, "code": self.code if left else "",
                    "expires_in": left if self.code else 0}

    def pair(self, code):
        with self.lock:
            if (not self.code or time.monotonic() >= self.expires or not isinstance(code, str) or
                    not AUTH.fullmatch(code) or not hmac.compare_digest(self.code, code)):
                return None
            self.code = None
            self.token, self.key = secrets.token_urlsafe(32), secrets.token_bytes(32)
            return self.token

    def identify(self, supplied):
        with self.lock:
            if self.token and AUTH.fullmatch(supplied) and hmac.compare_digest(supplied, self.token):
                return self.key
            return None

    def media_key(self):
        with self.lock:
            return self.key

    def leave(self, key):
        with self.lock:
            if self.key is key:
                self.token = self.key = None


class MobileServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = False


def handler_for(api, pairing):
    web_root = Path(api.storage["WEB_DIR"]).resolve()

    class Handler(LocalFilesHandler):
        def log_message(self, *_args):
            pass

        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                             "style-src 'self'; img-src 'self' blob:; media-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            super().end_headers()

        def _json(self, value, status=200):
            raw = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _same_origin(self):
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return (in_loopback(host) and (not origin or origin == "http://" + host) and
                    self.headers.get("Sec-Fetch-Site") != "cross-site")

        def do_GET(self):
            if not self._same_origin():
                self.send_error(403)
                return
            route = urlsplit(self.path)
            if route.query or route.fragment:
                self.send_error(404)
            elif route.path in SHELL:
                name, mime = SHELL[route.path]
                self._file(web_root / name, roots=(web_root,), content_type=mime)
            elif route.path.startswith("/media/"):
                self._chat_media(route.path)
            elif route.path.startswith("/avatar/"):
                self._avatar(route.path)
            else:
                self.send_error(404)

        def _avatar(self, route):
            parts = route.split("/")
            key = pairing.media_key()
            if len(parts) == 5 and key:
                _, _, role, name, supplied = parts
                source = f"/appearance/{role}/{name}"
                if (AVATAR_ROUTE.fullmatch(source) and re.fullmatch("[0-9a-f]{64}", supplied) and
                        hmac.compare_digest(supplied, seal(key, "avatar", source))):
                    api.appearance.serve_avatar(self, source)
                    return
            self.send_error(404)

        def _chat_media(self, route):
            components = route.split("/")
            if len(components) != 5:
                self.send_error(404)
                return
            _, _, kind, encoded, supplied = components
            name = unquote(encoded)
            key = pairing.media_key()
            if (key is None or kind not in MIME or not safe_name(name) or
                    not re.fullmatch("[0-9a-f]{64}", supplied) or
                    not hmac.compare_digest(supplied, seal(key, kind, name))):
                self.send_error(404)
                return
            archive = api.storage["媒体库"]
            if archive and ARCHIVED_MEDIA.fullmatch(name):
                path = archive.resolve_path(name) or archive.resolve_path(name, thumbnail=True)
                roots = (archive.objects_dir, archive.thumbs_dir, archive.trash_dir)
            else:
                root = Path(api.storage["聊媒体目录"])
                path, roots = root / name, (root,)
            mime = MIME[kind].get(path.suffix.lower()) if path else None
            self._file(path if mime else None, roots=roots, content_type=mime)

        def _body(self, maximum):
            if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
                raise ValueError
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= maximum:
                raise ValueError
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError
            return value

        def do_POST(self):
            if not self._same_origin():
                self._json({"ok": False}, 403)
                return
            route = urlsplit(self.path)
            routes = {"/api/session": self._session, "/api/history": self._history,
                      "/api/send": self._send, "/api/image": self._send_image, "/api/leave": self._leave}
            if route.query or route.fragment or route.path not in (*routes, "/api/pair"):
                self._json({"ok": False}, 404)
                return
            try:
                if route.path == "/api/pair":
                    token = pairing.pair(self._body(1024).get("code"))
                    if token:
                        self._json({"ok": True, "token": token})
                    else:
                        self._json({"ok": False, "error": "配对码无效或过期"}, 401)
                    return
                auth = self.headers.get("Authorization", "")
                key = pairing.identify(auth[7:]) if auth.startswith("Bearer ") else None
                if key is None:
                    self._json({"ok": False, "error": "请重新配对"}, 401)
                    return
                limits = media_limits(api.config.get("media_limits"))
                maximum = encoded_size(limits["image"]) + 256 * 1024 if route.path == "/api/image" else 256 * 1024
                value = self._body(maximum)
                if pairing.identify(auth[7:]) is not key:
                    self._json({"ok": False, "error": "请重新配对"}, 401)
                    return
                self._json(routes[route.path](value, key))
            except (TypeError, ValueError, UnicodeError, TimeoutError):
                self._json({"ok": False, "error": "请求格式无效或过大"}, 400)
            except Exception:
                self._json({"ok": False, "error": "操作结果未确认，请先核对聊天记录"}, 500)

        def _session(self, _body, key):
            profile = api.profile()
            limits = api.media_limits()["limits"]
            capabilities = profile.get("capabilities") or {}
            people = {}
            for role, default in (("me", "我"), ("peer", "聊天助手")):
                person = profile.get(role) or {}
                avatar = person.get("avatar") or ""
                url = f"/{role}.png"
                if AVATAR_ROUTE.fullmatch(avatar):
                    url = avatar.replace("/appearance/", "/avatar/", 1) + "/" + seal(key, "avatar", avatar)
                people[role] = {"name": person.get("display_name") or default, "avatar": url}
            return {"ok": True, "people": people,
                    "image_limit": limits.get("image", DEFAULT_LIMITS["image"]),
                    "can_image": capabilities.get("image") is True,
                    "can_text": capabilities.get("text") is True,
                    "connected": profile.get("status", {}).get("connected") is True}

        def _leave(self, _body, key):
            pairing.leave(key)
            return {"ok": True}

        def _history(self, body, key):
            before, after = body.get("before"), body.get("after")
            if (before is not None and after is not None or
                    any(value is not None and (type(value) is not int or value < 0 or value >= 2**53)
                        for value in (before, after))):
                raise ValueError
            if after is None:
                page = api.chat_history(PAGE_SIZE, 0, before)
            else:
                total = api.chat_history(1)["总"]
                if total <= after:
                    return {"ok": True, "total": total, "first": total, "next": total,
                            "messages": [], "reset": total < after}
                end = min(total, after + PAGE_SIZE)
                page = api.chat_history(end - after, 0, end)
            return {"ok": True, "total": page["总"], "first": page["前游标"], "next": page["上界"],
                    "messages": [message(key, row) for row in page["消息"]]}

        def _send(self, body, _key):
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 50000:
                raise ValueError
            if api.chat_ready().get("capabilities", {}).get("text") is not True:
                return {"ok": False, "error": "当前连接无法发送文字"}
            return sent(api.chat_send(text, ORIGIN))

        def _send_image(self, body, _key):
            image, name, text = body.get("image"), body.get("name"), body.get("text", "")
            if (not isinstance(image, str) or not isinstance(text, str) or len(text) > 50000 or
                    not safe_name(name) or Path(name).suffix.lower() not in MIME["image"]):
                raise ValueError
            if api.chat_ready().get("capabilities", {}).get("image") is not True:
                return {"ok": False, "error": "当前连接无法发送图片"}
            return sent(api.chat_send_image(image, name, text, ORIGIN))

    return Handler


def sent(result):
    ok, recorded = result.get("ok") is True, bool(result.get("message_id"))
    if ok:
        return {"ok": True}
    return {"ok": False, "recorded": recorded, "error": (
        "消息已保存在本机，请在聊天记录里核对发送状态" if recorded else "消息未能发送，草稿已保留")}


class MobileChat:
    def __init__(self, api):
        self.api = api
        self.lock = threading.RLock()
        self.server = self.pairing = self.thread = None
        self.closed = False

    def view(self):
        with self.lock:
            if self.server is None:
                return {"ok": True, "enabled": False}
            port = self.server.server_address[1]
            return {"ok": True, "enabled": True, "port": port, "url": f"http://127.0.0.1:{port}/",
                    **self.pairing.view()}

    def manage(self, action, port=None):
        with self.lock:
            if self.closed:
                return {"ok": False, "error": "小窝正在退出"}
            if action == "stop":
                self._stop()
            elif action == "renew" and self.pairing:
                self.pairing.renew()
            elif action == "start" and self.server is None:
                if port is not None and (type(port) is not int or not 1024 <= port <= 65535):
                    return {"ok": False, "error": "端口应为 1024 到 65535，或留空"}
                pairing = Pairing()
                try:
                    listener = MobileServer(("127.0.0.1", port or 0), handler_for(self.api, pairing))
                except OSError:
                    return {"ok": False, "error": "端口无法使用，请换一个或留空"}
                self.server, self.pairing = listener, pairing
                self.thread = threading.Thread(target=listener.serve_forever, name="mobile-chat", daemon=True)
                self.thread.start()
            else:
                return {"ok": False, "error": "手机入口状态已改变，请重新读取"}
            return self.view()

    def _stop(self):
        if self.server is not None:
            self.pairing.revoke()
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = self.pairing = self.thread = None

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()
