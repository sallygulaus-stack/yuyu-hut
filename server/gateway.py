"""Loopback gateway for dsh-client/1, using the independent OneBot adapter.

This transports messages only. Personality, memory and tools remain in DSH.
The synthetic framework used in tests is deliberately a separate process.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import http.server
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from media_limits import media_limits, encoded_size, request_limit, size_label, LEGACY_ONEBOT_LIMITS

VERSION = "dsh-client/1"
CAPABILITIES = dict.fromkeys((
    "text", "image", "record", "media_ids", "profiles", "event_resume",
    "deduplication", "idempotent_send", "media_upload", "media_download", "http_poll", "message_status",
), True)
CAPABILITIES["delivery_receipts"] = False


class MediaRejected(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


class Gateway:
    def __init__(self, config):
        self.config = config
        self.media_limits = media_limits(config.get("media_limits"))
        self.max_body = request_limit(self.media_limits)
        self.root = Path(config["data_dir"])
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.faults = {}
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS outbox (identity TEXT PRIMARY KEY, payload TEXT, sent INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity TEXT UNIQUE, payload TEXT);
                CREATE TABLE IF NOT EXISTS acks (sequence INTEGER PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, sha TEXT, mime TEXT, name TEXT);
            """)
            row = db.execute("SELECT value FROM settings WHERE key='endpoint'").fetchone()
            self.endpoint_id = row[0] if row else "gw_" + uuid.uuid4().hex
            db.execute("INSERT OR IGNORE INTO settings VALUES ('endpoint',?)", (self.endpoint_id,))
            row = db.execute("SELECT value FROM settings WHERE key='signing'").fetchone()
            self.signing = row[0] if row else secrets.token_hex(32)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('signing',?)", (self.signing,))
        self.thread = threading.Thread(target=self.drain, daemon=True)

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.root / "gateway.sqlite3")
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA synchronous=FULL")
                with db:
                    yield db
            finally:
                db.close()

    def onebot(self, route, body=None):
        raw = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.config["onebot_url"] + route, data=raw, headers={
            "Authorization": "Bearer " + self.config["token"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as res:
            return json.load(res)

    def identity(self, body):
        return json.dumps([str(body.get("endpoint_id") or self.endpoint_id),
                           str(body.get("conversation_id") or "main"),
                           str(body.get("message_id") or "")], separators=(",", ":"))

    def media_info(self, media_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
        if not row:
            raise ValueError("unknown media")
        expires = int(time.time()) + 60
        signature = hmac.new(self.signing.encode(), f"{media_id}:{expires}".encode(),
                             hashlib.sha256).hexdigest()
        return {"ok": True, "media_id": media_id, "sha256": row["sha"], "mime": row["mime"],
                "name": row["name"], "expires_at": expires,
                "url": f"/v1/media-content/{media_id}?expires={expires}&sig={signature}"}

    def upload(self, body):
        encoded = str(body.get("b64") or "")
        mime = str(body.get("mime") or "application/octet-stream")
        kind = "record" if mime.startswith("audio/") or body.get("type") == "record" else "image"
        limit = self.media_limits[kind]
        if len(encoded) > encoded_size(limit):
            raise MediaRejected("media_too_large", "单个媒体不能超过 " + size_label(limit), 413)
        raw = base64.b64decode(encoded, validate=True)
        sha = hashlib.sha256(raw).hexdigest()
        if len(raw) > limit:
            raise MediaRejected("media_too_large", "单个媒体不能超过 " + size_label(limit), 413)
        if not raw or body.get("sha256", sha) != sha:
            raise MediaRejected("invalid_media", "媒体为空或内容校验失败")
        media_id = "gm_" + sha
        dest = self.root / "media" / media_id
        if not dest.exists():
            with dest.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                import os
                os.fsync(handle.fileno())
        name = Path(str(body.get("name") or "media")).name
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO media VALUES (?,?,?,?)", (media_id, sha, mime, name))
        return self.media_info(media_id)

    def profiles(self):
        state = self.onebot("/__state")
        return {
            "onebot": {
                "me": {"identity": str(state["peerId"]), "display_name": state.get("peerName", "测试用户")},
                "peer": {"identity": str(state["selfId"]), "display_name": state.get("selfName", "测试框架"),
                         "version": state.get("version", "")}},
            "dsh": self.config.get("synthetic_profiles", {}),
        }, state

    def capabilities(self):
        restrictions = self.config.get("capabilities", {})
        caps = {key: enabled and restrictions.get(key, True) is True
                for key, enabled in CAPABILITIES.items()}
        if self.config.get("test_controls"):
            if self.faults.get("restrict_media"):
                caps.update(image=False, record=False, media_upload=False, media_download=False)
            if self.faults.get("no_idempotency"):
                caps["idempotent_send"] = False
        return caps

    def dispatch(self, route, body, query):
        if route == "/v1/handshake":
            if body.get("protocol_version") != VERSION:
                raise ValueError("incompatible protocol")
            profiles, state = self.profiles()
            caps = self.capabilities()
            onebot_limits = media_limits(state.get("media_limits"), LEGACY_ONEBOT_LIMITS)
            return {"ok": True, "type": "hello_ack", "protocol_version": VERSION,
                    "transport": "http_poll", "endpoint_id": self.endpoint_id, "capabilities": caps,
                    "profiles": profiles, "version": "onebot-gateway/1",
                    "media_limits": {kind: min(self.media_limits[kind], onebot_limits[kind])
                                     for kind in self.media_limits},
                    "framework_connected": bool(state.get("connections"))}
        if route == "/v1/heartbeat":
            state = self.onebot("/__state")
            return {"ok": True, "server_time": time.time(), "endpoint_id": self.endpoint_id,
                    "framework_connected": bool(state.get("connections"))}
        if route == "/v1/messages":
            if (body.get("endpoint_id") != self.endpoint_id or body.get("protocol_version") != VERSION or not all(
                    isinstance(body.get(k), str) and body[k] for k in
                    ("message_id", "event_id", "conversation_id")) or not isinstance(body.get("segments"), list)):
                raise ValueError("invalid message")
            caps = self.capabilities()
            for seg in body["segments"]:
                if seg.get("type") not in {"text", "image", "record", "face"}:
                    raise ValueError("unsupported segment")
                if seg["type"] in {"image", "record"}:
                    if not caps.get(seg["type"]):
                        raise ValueError("unsupported capability")
                    self.media_info(seg.get("media_id"))
                    if any(k in seg for k in ("b64", "url", "文件")):
                        raise ValueError("media must use gateway identity")
            identity = self.identity({**body, "endpoint_id": self.endpoint_id})
            payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
            with self.db() as db:
                old = db.execute("SELECT payload FROM outbox WHERE identity=?", (identity,)).fetchone()
                if old:
                    previous = json.loads(old[0])
                    immutable = ("conversation_id", "message_id", "event_id", "created_at",
                                 "sender", "segments", "attachments", "protocol_version")
                    if any(previous.get(k) != body.get(k) for k in immutable):
                        raise ValueError("identity conflict")
                db.execute("INSERT OR IGNORE INTO outbox(identity,payload) VALUES (?,?)", (identity, payload))
            reply = {"ok": True, "accepted": True, "endpoint_id": self.endpoint_id,
                     "delivery_status": "gateway_accepted", **{k: body[k] for k in
                                                      ("message_id", "event_id", "conversation_id")}}
            if self.faults.pop("wrong_receipt", False):
                reply["message_id"] = "wrong"
            if self.faults.pop("drop_receipt", False):
                return None
            return reply
        if route == "/v1/message-status":
            if body.get("endpoint_id") != self.endpoint_id:
                raise ValueError("endpoint mismatch")
            identity = self.identity(body)
            with self.db() as db:
                row = db.execute("SELECT payload,sent FROM outbox WHERE identity=?", (identity,)).fetchone()
            if not row or json.loads(row["payload"])["event_id"] != body.get("event_id"):
                raise ValueError("unknown identity")
            status, detail = "gateway_accepted", "网关已保存，等待交给假端"
            if row["sent"]:
                status, detail = "framework_unknown", "假端暂不可用；框架处理结果未知，不自动重放"
                try:
                    result = self.onebot("/__delivery", body)
                    if (result.get("ok") is not True or result.get("accepted") is not True or
                            any(result.get(k) != body[k] for k in ("message_id", "event_id")) or
                            result.get("delivery_status") not in {"framework_waiting", "framework_unknown"}):
                        raise ValueError("invalid delivery status")
                    status, detail = result["delivery_status"], result.get("delivery_detail", "")
                except Exception:
                    pass
            return {"ok": True, **{k: body[k] for k in
                    ("message_id", "event_id", "conversation_id", "endpoint_id")},
                    "delivery_status": status, "delivery_detail": detail,
                    "framework_status": {"framework_waiting": "waiting", "framework_unknown": "unknown"}.get(status, "")}
        if route == "/v1/events":
            cursor = int(query.get("resume_from", ["0"])[0])
            with self.db() as db:
                rows = db.execute("SELECT sequence,payload FROM events WHERE sequence>? ORDER BY sequence LIMIT 100",
                                  (cursor,)).fetchall()
            events = [{**json.loads(row["payload"]), "sequence": row["sequence"]} for row in rows]
            if self.faults.get("duplicate_events") and events:
                events = [events[0], *events]
            return {"ok": True, "events": events}
        if route == "/v1/acks":
            if self.faults.get("fail_ack"):
                raise ValueError("synthetic ack failure")
            with self.db() as db:
                row = db.execute("SELECT payload FROM events WHERE sequence=?", (body.get("sequence"),)).fetchone()
                if not row:
                    raise ValueError("unknown ack")
                event = json.loads(row[0])
                if (body.get("endpoint_id") != self.endpoint_id or
                        any(event[k] != body.get(k) for k in ("message_id", "event_id", "conversation_id"))):
                    raise ValueError("ack identity mismatch")
                db.execute("INSERT OR REPLACE INTO acks VALUES (?,?)",
                           (body["sequence"], json.dumps(body)))
            return {"ok": True, **body}
        if route == "/v1/media":
            if not self.capabilities().get("media_upload"):
                raise ValueError("media upload disabled")
            return self.upload(body)
        if route.startswith("/v1/media/"):
            return self.media_info(route.removeprefix("/v1/media/"))
        if route == "/say":
            if not body.get("message_id") or not body.get("event_id"):
                raise ValueError("callback requires stable identity")
            segments = []
            for seg in body.get("段") or []:
                if seg.get("type") in {"image", "record"}:
                    info = self.upload(seg)
                    segments.append({"type": seg["type"], **{k: info[k] for k in ("media_id", "mime", "sha256")}})
                else:
                    segments.append(seg)
            event = {"message_id": str(body["message_id"]), "event_id": str(body["event_id"]),
                     "conversation_id": str(body.get("conversation_id") or "main"),
                     "segments": segments, "attachments": [s for s in segments if s.get("media_id")],
                     "created_at": body.get("created_at") or time.time(), "sender": body.get("sender") or {},
                     "delivery_status": "delivered", "protocol_version": VERSION,
                     "source_protocol_version": "onebot-v11", "endpoint_id": self.endpoint_id,
                     "capabilities": CAPABILITIES}
            identity = self.identity(body)
            with self.db() as db:
                db.execute("INSERT OR IGNORE INTO events(identity,payload) VALUES (?,?)",
                           (identity, json.dumps(event, ensure_ascii=False)))
            return {"ok": True, "message_id": str(body["message_id"]), "event_id": str(body["event_id"])}
        if self.config.get("test_controls") and route == "/__test/faults":
            self.faults.update(body)
            return {"ok": True}
        if self.config.get("test_controls") and route == "/__test/stats":
            with self.db() as db:
                stats = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                         for table in ("events", "outbox", "acks", "media")}
            return {"ok": True, **stats}
        raise ValueError("unknown route")

    def drain(self):
        while not self.stop.wait(.25):
            try:
                with self.db() as db:
                    row = db.execute("SELECT identity,payload FROM outbox WHERE sent=0 LIMIT 1").fetchone()
                if not row:
                    continue
                value = json.loads(row["payload"])
                segments = []
                for seg in value["segments"]:
                    if seg.get("media_id"):
                        info = self.media_info(seg["media_id"])
                        raw = (self.root / "media" / seg["media_id"]).read_bytes()
                        segments.append({"type": seg["type"], "b64": base64.b64encode(raw).decode(),
                                         "mime": info["mime"], "name": info["name"]})
                    else:
                        segments.append(seg)
                result = self.onebot("/__inject", {**value, "segments": segments,
                                                   "text": "", "client_message_id": value["message_id"]})
                if (result.get("ok") is not True or result.get("accepted") is not True or
                        result.get("message_id") != value["message_id"] or
                        result.get("event_id") != value["event_id"] or
                        result.get("delivery_status") not in {"framework_waiting", "framework_unknown"}):
                    raise ValueError("OneBot acceptance mismatch")
                with self.db() as db:
                    db.execute("UPDATE outbox SET sent=1 WHERE identity=?", (row["identity"],))
            except Exception:
                self.stop.wait(.5)

    def handler(self):
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def send_json(self, value, status=200):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def handle_request(self):
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, "Bearer " + gateway.config["token"]):
                    self.send_json({"ok": False, "error": "authentication failed"}, 401)
                    return
                try:
                    parsed = urllib.parse.urlsplit(self.path)
                    query = urllib.parse.parse_qs(parsed.query)
                    if parsed.path.startswith("/v1/media-content/"):
                        media_id = parsed.path.removeprefix("/v1/media-content/")
                        gateway.media_info(media_id)
                        expiry = int(query.get("expires", ["0"])[0])
                        signature = hmac.new(gateway.signing.encode(), f"{media_id}:{expiry}".encode(),
                                             hashlib.sha256).hexdigest()
                        if expiry < time.time() or not hmac.compare_digest(
                                signature, query.get("sig", [""])[0]):
                            raise ValueError("expired media")
                        raw = (gateway.root / "media" / media_id).read_bytes()
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(raw)))
                        self.send_header("Content-Type", "application/octet-stream")
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    size = int(self.headers.get("Content-Length") or 0)
                    if size < 0 or size > gateway.max_body:
                        self.close_connection = True
                        raise MediaRejected("request_too_large", "请求超过此网关的媒体容量", 413)
                    body = json.loads(self.rfile.read(size)) if size else {}
                    if not isinstance(body, dict):
                        raise ValueError("invalid body")
                    reply = gateway.dispatch(parsed.path, body, query)
                    if reply is None:
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                    self.send_json(reply)
                except MediaRejected as exc:
                    self.send_json({"ok": False, "error": str(exc), "error_code": exc.code,
                                    "retryable": False}, exc.status)
                except (ValueError, KeyError, TypeError):
                    self.send_json({"ok": False, "error": "回调内容无效，请核对媒体格式及消息身份",
                                    "error_code": "invalid_callback", "retryable": False}, 400)
                except Exception:
                    self.send_json({"ok": False, "error": "gateway unavailable"}, 503)

            do_GET = handle_request
            do_POST = handle_request
        return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if not config.get("token"):
        raise SystemExit("Gateway requires an isolated token")
    if not config.get("data_dir") or not Path(config["data_dir"]).is_absolute():
        raise SystemExit("Gateway requires an explicit persistent absolute data_dir")
    gateway = Gateway(config)
    servers = []
    for key, secure in (("port", False), ("tls_port", True)):
        if key not in config:
            continue
        server = http.server.ThreadingHTTPServer(("127.0.0.1", int(config[key])), gateway.handler())
        server.daemon_threads = True
        if secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(config["cert_file"], config["key_file"])
            server.socket = context.wrap_socket(server.socket, server_side=True)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    gateway.thread.start()
    print(json.dumps({"ready": True, "ports": [s.server_address[1] for s in servers]}), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
