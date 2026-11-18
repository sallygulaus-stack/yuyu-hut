"""Loopback UI, bounded RPC, media ranges and optional OneBot receive endpoint."""
import hmac
import http.server
import json
import mimetypes
from pathlib import Path
import re
import secrets
import urllib.parse

from appearance import MAX_APPEARANCE_BODY
from diary import MAX_PHOTO
from song_import import MAX_SONG_BODY

RPC_METHODS = frozenset({
    "profile", "my_profile", "peer_profile", "profile_appearance", "state", "chat_ready",
    "media_limits", "chat_send", "chat_send_image",
    "chat_history", "chat_poll", "chat_search", "new_pond", "chat_retry", "chat_send_record", "rebind_endpoint",
    "sticker_list", "sticker_add", "sticker_del", "sticker_send",
    "gallery_list", "gallery_status", "gallery_add", "gallery_del",
    "gallery_restore", "gallery_export", "gallery_collect",
    "media_settings", "media_update_settings", "media_maintenance", "media_trash",
    "media_restore", "media_set_permanent", "media_delete", "song_list", "song_import",
    "get_skin", "set_skin", "comfy_submit", "comfy_collect", "test_window",
    "diary_dates", "diary_read", "diary_image", "diary_add_photo", "diary_save", "pond_status",
    "mobile_view", "mobile_manage",
    "console_view", "console_diagnostics", "console_chat", "console_bridge", "console_service",
    "console_sample", "console_save_image", "console_open_data",
})
MAX_BODY = 140 * 1024 * 1024
RPC_BODY_LIMITS = {"profile_appearance": MAX_APPEARANCE_BODY, "song_import": MAX_SONG_BODY,
                   "diary_add_photo": (MAX_PHOTO + 2) // 3 * 4 + 1024,
                   "mobile_view": 1024, "mobile_manage": 1024}


class LoopbackServer(http.server.ThreadingHTTPServer):
    # The browser fetches several scripts and styles at once on a cold start.
    request_queue_size = 64


class LocalFilesHandler(http.server.BaseHTTPRequestHandler):
    def _file(self, path, *, roots, download_name=None, content_type=None):
        if path is None:
            self.send_error(404)
            return
        path = Path(path).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in roots) or not path.is_file():
            self.send_error(404)
            return
        length = path.stat().st_size
        start, end = 0, length - 1
        status = 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", requested.strip())
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), length - 1) if last else length - 1
            else:
                start = max(0, length - int(last))
            if start > end or start >= length:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % length)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        try:
            handle = path.open("rb")
        except OSError:
            self.send_error(404)
            return
        with handle:
            self.send_response(status)
            self.send_header("Content-Type", content_type or mimetypes.guess_type(path)[0] or
                             "application/octet-stream")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, length))
            if download_name:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" +
                                 urllib.parse.quote(download_name, safe=""))
            self.end_headers()
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def create_server(storage, api, port=None):
    session_token = secrets.token_urlsafe(32)
    web_root = Path(storage["WEB_DIR"]).resolve()
    data_routes = {
        "/assets/画册/": storage["画册目录"],
        "/assets/画册缓存/": storage["画册缓存目录"],
        "/assets/画册收纳/": storage["画册收纳目录"],
        "/assets/表情包/": storage["表情包目录"],
        "/assets/食物/": storage["食物目录"],
        "/assets/歌/": storage["歌目录"],
    }

    class Handler(LocalFilesHandler):
        def log_message(self, *_args):
            pass

        def _local(self):
            expected = "127.0.0.1:%d" % self.server.server_address[1]
            if self.headers.get("Host") not in (expected, "localhost:%d" % self.server.server_address[1]):
                self.send_error(403)
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send_error(403)
                return False
            return True

        def _json(self, payload, status=200):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _media(self, name, thumbnail=False):
            if not name or name != Path(name).name or "/" in name or "\\" in name:
                return None
            archive = storage.get("媒体库")
            if archive and name.startswith("m_"):
                result = archive.resolve_path(name, thumbnail=thumbnail)
                if not result and not thumbnail:
                    result = archive.resolve_path(name, thumbnail=True)
                return Path(result) if result else None
            if thumbnail:
                thumb = storage["_媒体小图"](name)
                if thumb:
                    return Path(storage["聊缩略目录"]) / thumb
            return Path(storage["聊媒体目录"]) / name

        def _download(self, name, stored=False):
            if name.startswith("m_"):
                archive = storage.get("媒体库")
                item = archive.get_media(name) if archive else None
                path = archive.resolve_path(name, thumbnail=False) if item else None
                filename = (Path(item.get("display_name") or name).stem +
                            storage["_归档实际后缀"](path, item)) if path else name
                return path, filename, [archive.root] if archive else []
            storage["_画册安全名"](name)
            root = storage["画册收纳目录"] if stored else storage["画册目录"]
            return Path(root) / name, storage["_画册原名"](name, stored), [root]

        def do_GET(self):
            if not self._local():
                return
            parts = urllib.parse.urlsplit(self.path)
            route = urllib.parse.unquote(parts.path)
            if route == "/api/bootstrap":
                self._json({"ok": True, "session_token": session_token, "profile": api.profile()})
            elif route == "/health":
                self._json({"ok": True, "mode": api.config["mode"]})
            elif route.startswith("/appearance/"):
                api.appearance.serve_avatar(self, route)
            elif route.startswith("/media/"):
                query = urllib.parse.parse_qs(parts.query)
                archive = storage.get("媒体库")
                self._file(self._media(route[7:], query.get("s") == ["1"]),
                           roots=[archive.root if archive else storage["媒体归档目录"],
                                  storage["聊媒体目录"], storage["聊缩略目录"]])
            elif route.startswith("/download/"):
                try:
                    path, name, roots = self._download(route.removeprefix("/download/"),
                                                       "stored=1" in parts.query)
                    self._file(path, roots=roots, download_name=name)
                except Exception:
                    self.send_error(404)
            elif route in ("/", "/index.html"):
                self._file(web_root / "index.html", roots=[web_root])
            elif route.startswith("/assets/"):
                for prefix, root in data_routes.items():
                    if route.startswith(prefix):
                        self._file(Path(root) / route[len(prefix):], roots=[root])
                        break
                else:
                    self._file(web_root / route.lstrip("/"), roots=[web_root])
            else:
                self.send_error(404)

        def do_POST(self):
            if not self._local():
                return
            parts = urllib.parse.urlsplit(self.path)
            if parts.path.startswith("/api/"):
                origin = self.headers.get("Origin")
                valid_origin = "http://" + self.headers.get("Host", "")
                if (origin and origin != valid_origin) or not hmac.compare_digest(
                    self.headers.get("X-Panel-Token", ""), session_token
                ):
                    self._json({"ok": False, "error": "请求身份校验失败"}, 403)
                    return
                method = urllib.parse.unquote(parts.path.removeprefix("/api/"))
                if method not in RPC_METHODS:
                    self._json({"ok": False, "error": "没有此接口"}, 404)
                    return
            elif parts.path == "/say" and api.config["mode"] == "onebot":
                token = str(api.config["onebot"].get("token") or "")
                supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if not supplied:
                    supplied = urllib.parse.parse_qs(parts.query).get("access_token", [""])[0]
                if token and not hmac.compare_digest(token, supplied):
                    self._json({"ok": False}, 403)
                    return
                method = None
            else:
                self._json({"ok": False}, 404)
                return
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if method and method.startswith("console_") and size > 8192:
                    self._json({"ok": False, "error": "设置请求过大"}, 413)
                    return
                maximum = RPC_BODY_LIMITS.get(method, MAX_BODY)
                if not 0 < size <= maximum:
                    self._json({"ok": False, "error": "请求大小超限"}, 413)
                    return
                arguments = json.loads(self.rfile.read(size))
                if method is None:
                    result = api.receive(arguments)
                elif not isinstance(arguments, list) or len(arguments) > 12:
                    raise ValueError("参数格式错误")
                elif method == "gallery_export" and not storage["webview"].windows:
                    name = str(arguments[0])
                    stored = bool(arguments[1]) if len(arguments) > 1 else False
                    path, filename, _ = self._download(name, stored)
                    result = {"ok": bool(path and Path(path).is_file()),
                              "download": "/download/" + urllib.parse.quote(name, safe="") +
                              ("?stored=1" if stored else ""), "filename": filename}
                else:
                    result = getattr(api, method)(*arguments)
                self._json(result)
            except (TypeError, ValueError, KeyError, IndexError):
                self._json({"ok": False, "error": "请求参数无效"}, 400)
            except Exception:
                self._json({"ok": False, "error": "操作未完成，请重试"}, 500)

    return LoopbackServer(("127.0.0.1", port if port is not None else 0), Handler)
