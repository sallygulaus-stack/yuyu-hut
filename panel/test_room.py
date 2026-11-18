"""Opt-in local text-only bridge feed. No production callback or private chat is reused."""
from contextlib import closing
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def _get(base, path, payload=None, headers=None):
    target = urlsplit(base)
    if (target.scheme, target.hostname) != ("http", "127.0.0.1") or target.path not in ("", "/") or (
            target.query or target.fragment or target.username or target.password or not target.port):
        raise ValueError("测试服务必须在本机")
    body = json.dumps(payload).encode() if payload is not None else None
    with closing(HTTPConnection("127.0.0.1", target.port, timeout=4)) as conn:
        conn.request("POST" if body is not None else "GET", path, body, {"Content-Type": "application/json", **(headers or {})})
        with conn.getresponse() as response:
            if response.status != 200:
                raise OSError("测试服务暂不可用")
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("测试记录过大")
            return json.loads(raw)


class TestRoom:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.feed = env.get("HUT_TEST_FEED", "")
        self.bridge = env.get("HUT_TEST_BRIDGE", "")
        self.peer = env.get("HUT_TEST_PEER", "")
        self.authfile = env.get("HUT_TEST_BRIDGE_TOKEN_FILE", "")
        self.feed_token = env.get("HUT_TEST_FEED_TOKEN", "")

    def enabled(self):
        return bool(self.feed and self.bridge and self.peer.isdigit() and self.authfile)

    def feed_request(self, route, body=None):
        return _get(self.feed, route, body, {"Authorization": "Bearer " + self.feed_token} if self.feed_token else {})

    def connected(self):
        token = Path(self.authfile).read_text(encoding="utf-8").strip()
        if not token:
            return False
        status = _get(self.bridge, "/api/status", headers={"x-console-token": token})
        state = self.feed_request("/__state")
        upstream = status.get("snowluma") or {}
        return (status.get("mode") == "closed-agent" and str(status.get("ownerQQ")) == self.peer
                and [str(p) for p in status.get("allowPrivate", [])] == [self.peer]
                and not status.get("allowGroups") and status.get("dshReady") is True
                and upstream.get("connected") is True and upstream.get("good") is not False
                and upstream.get("accountOnline") is not False and state.get("ok") is True
                and str(state.get("peerId")) == self.peer and int(state.get("connections", 0)) == 1)

    def call(self, method, args):
        if method == "configured":
            return {"ok": True, "enabled": self.enabled()}
        offline = {"ok": False, "connected": False, "code": "route_unavailable", "error": "本机测试未接线"}
        if not self.enabled() or method not in ("send", "poll", "history") or not isinstance(args, list):
            return offline
        try:
            if method == "send":
                if len(args) != 1 or not isinstance(args[0], str) or not 0 < len(args[0].strip()) <= 2000:
                    return {"ok": False, "error": "请发送 1 到 2000 字的测试文字"}
                if not self.connected():
                    return offline
                try:
                    reply = self.feed_request("/__inject", {"text": args[0].strip(), "conversation_id": "main"})
                    if reply.get("ok") is True and reply.get("message_id") and type(reply.get("记录序")) is int:
                        return {"ok": True, "conversation_id": "trial-test"}
                except (HTTPException, ValueError, OSError):
                    pass
                return {"ok": False, "error": "提交结果未确认，请核对测试记录，不会自动重发"}
            state = self.feed_request("/__state")
            if state.get("ok") is not True or str(state.get("peerId")) != self.peer:
                return offline
            history = self.feed_request("/__history")
            if history.get("ok") is not True or not history.get("history_id"):
                return offline
            cursor, key = int(history["current"]), history["history_id"]
            rows = [{"序": x["序"], "记录序": x["序"], "谁": x["从"], "时": x["时"],
                     "message_id": x["message_id"], "段": [{"type": "text", "text": x["文"]}]}
                    for x in history["条"]]
            if method == "poll":
                since, previous = args
                reset = previous != key or since > cursor
                try:
                    connected = self.connected()
                except (OSError, HTTPException, ValueError):
                    connected = False
                return {"ok": True, "connected": connected, "reset": reset, "current": cursor,
                        "来信": [] if reset else [x for x in rows if x["序"] > since]}
            count, skip, *before = args
            upper = min(cursor, int(before[0])) if before else cursor - max(0, int(skip))
            eligible = [x for x in rows if x["序"] <= upper]
            page = eligible[-min(500, max(1, int(count))):]
            return {"ok": True, "消息": page, "前游标": page[0]["序"] - 1 if page else 0,
                    "还有更早": len(page) < len(eligible), "live_cursor": cursor, "history_id": key}
        except (HTTPException, OSError, KeyError, TypeError, ValueError, IndexError):
            return offline
