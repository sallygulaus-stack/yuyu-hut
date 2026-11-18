"""Client for this project's gateway protocol, not the native DSH API."""
from __future__ import annotations

import base64
import hashlib
import time
import uuid
from typing import Mapping
from urllib.parse import urlparse, quote

from core.models import CAPABILITIES, ConnectionStatus, PROTOCOL_VERSION
from .base import Connector, ConnectorError
from .http import request_json
from media_limits import media_limits, LEGACY_GATEWAY_LIMITS, size_label


def require_ok(reply):
    if not isinstance(reply, Mapping) or reply.get("ok") is not True or reply.get("error"):
        raise ConnectorError("网关没有返回明确成功确认")
    return reply


class MemoryRemoteTransport:
    """Replayable fixture with the same commit/ack semantics as the network."""
    def __init__(self, endpoint_id="remote-sim"):
        self.endpoint_id = endpoint_id
        self.received = []
        self.incoming = []
        self.acks = []
        self.connected = True
        self.protocol_version = PROTOCOL_VERSION
        self.capabilities = dict(CAPABILITIES)
        self.capabilities["message_status"] = False
        self.media = {}
        self.identities = {}
        self.fail_ack = False
        self.drop_receipt = False

    def check(self):
        if not self.connected:
            raise ConnectorError("模拟远端已断开")

    def handshake(self, hello):
        self.check()
        return {"ok": True, "type": "hello_ack", "endpoint_id": self.endpoint_id,
                "protocol_version": self.protocol_version, "capabilities": dict(self.capabilities),
                "transport": "http_poll", "profiles": {}, "resume_from": hello.get("resume_from", 0)}

    def send(self, message):
        self.check()
        key = (message["conversation_id"], message["message_id"])
        if key not in self.identities:
            self.identities[key] = dict(message)
            self.received.append(dict(message))
        if self.drop_receipt:
            self.drop_receipt = False
            raise ConnectorError("模拟回执丢失")
        return {"ok": True, "accepted": True, "message_id": message["message_id"],
                "event_id": message["event_id"], "conversation_id": message["conversation_id"],
                "endpoint_id": self.endpoint_id, "delivery_status": "gateway_accepted"}

    def push(self, event):
        self.incoming.append(dict(event))

    def poll(self, resume_from=0):
        self.check()
        return [dict(e) for e in self.incoming if int(e["sequence"]) > resume_from]

    def ack(self, body):
        self.check()
        if self.fail_ack:
            raise ConnectorError("模拟确认失败")
        self.acks.append(body["event_id"])
        return {"ok": True, **body}

    def heartbeat(self):
        self.check()
        return {"ok": True, "server_time": time.time()}

    def close(self):
        self.connected = False


class RemoteConnector(Connector):
    kind = "dsh_remote"

    def __init__(self, config=None, transport=None):
        super().__init__(config)
        self.transport = transport
        self._resume_from = 0
        self._hello = None
        self._last_status = "configured" if self.config.get("enabled") else "disabled"
        self._last_error = ""
        self._secure = False
        self.bound_endpoint_id = ""
        self.observed_endpoint_id = ""
        self.endpoint_changed = False

    @property
    def endpoint_id(self):
        return str(self.config.get("endpoint_id") or self.kind)

    def _validate_url(self):
        parsed = urlparse(str(self.config.get("base_url") or ""))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConnectorError("网关地址不能包含凭据、查询或片段")
        if parsed.scheme == "https":
            return True
        if (self.config.get("allow_insecure_local") and
                parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.scheme == "http"):
            return False
        raise ConnectorError("网关仅实现 HTTPS 轮询；HTTP 仅允许显式开启的回环测试")

    def connect(self, resume_from=None):
        if not self.config.get("enabled"):
            raise ConnectorError("连接器未启用")
        if resume_from is not None:
            self._resume_from = int(resume_from)
        self._last_status = "connecting"
        try:
            self._secure = self._validate_url() if self.transport is None else False
            hello = {"type": "hello", "protocol_version": PROTOCOL_VERSION,
                     "resume_from": self._resume_from, "capabilities": dict(CAPABILITIES)}
            reply = require_ok(self.transport.handshake(hello) if self.transport is not None
                               else request_json(self.config, "/v1/handshake", hello))
            if (reply.get("type") != "hello_ack" or reply.get("protocol_version") != PROTOCOL_VERSION
                    or not reply.get("endpoint_id") or reply.get("transport") != "http_poll"):
                raise ConnectorError("网关协议版本或传输方式不兼容")
            self.observed_endpoint_id = str(reply["endpoint_id"])
            if self.bound_endpoint_id and self.bound_endpoint_id != self.observed_endpoint_id:
                self.endpoint_changed = True
                self._hello = None
                self._last_status = "endpoint_changed"
                self._last_error = "网关数据身份已改变。请在控制台的聊天连接中重新绑定。"
                raise ConnectorError(self._last_error)
            self.endpoint_changed = False
            caps = reply.get("capabilities")
            if not isinstance(caps, dict) or not caps.get("text") or not caps.get("http_poll"):
                raise ConnectorError("网关缺少必需的文字/轮询能力")
            self._hello = {**reply, "capabilities": {
                k: bool(v is True and CAPABILITIES.get(k)) for k, v in caps.items()
                if k in CAPABILITIES}}
            self._hello["media_limits"] = media_limits(reply.get("media_limits"), LEGACY_GATEWAY_LIMITS)
            self._last_status = "connected"
            self._last_error = ""
            return self.status()
        except Exception as exc:
            self.disconnected(exc)
            raise

    def disconnected(self, exc):
        self._hello = None
        if not self.endpoint_changed:
            self._last_status = "error"
            self._last_error = "网关连接或协议校验失败"

    def reconnect(self):
        self._hello = None
        return self.connect()

    def ensure_connected(self):
        if self._hello is None:
            self.connect()

    def status(self):
        hello = self._hello
        caps = dict(hello.get("capabilities") or {}) if hello else {}
        degraded = bool(hello and (not caps.get("idempotent_send") or
                                  hello.get("framework_connected") is False))
        phase = "degraded" if degraded else self._last_status
        return ConnectionStatus(
            phase=phase, configured=bool(self.config.get("enabled")),
            connected=hello is not None, degraded=degraded,
            connections=int(hello is not None), endpoint_id=self.endpoint_id,
            label=("网关已连接，等待框架" if hello.get("framework_connected") is False else "网关已连接")
            if hello else "网关" + {
                "configured": "已配置", "disabled": "未启用", "connecting": "连接中",
                "error": "连接错误", "endpoint_changed": "身份已变化，需重新绑定"}.get(phase, "已断开"),
            error=self._last_error, capabilities=caps,
            endpoint_changed=self.endpoint_changed, bound_endpoint_id=self.bound_endpoint_id,
            observed_endpoint_id=self.observed_endpoint_id,
        ).to_dict() | {"secure": self._secure, "profiles": (hello or {}).get("profiles", {}),
                      "version": (hello or {}).get("version", ""),
                      "framework_connected": (hello or {}).get("framework_connected"),
                      "media_limits": (hello or {}).get("media_limits") or dict(LEGACY_GATEWAY_LIMITS),
                      "gateway_id": (hello or {}).get("endpoint_id", "")}

    def heartbeat(self):
        self.ensure_connected()
        result = require_ok(self.transport.heartbeat() if self.transport is not None else
                            request_json(self.config, "/v1/heartbeat", {}))
        if "framework_connected" in result:
            self._hello["framework_connected"] = result["framework_connected"]
        if result.get("endpoint_id") and result["endpoint_id"] != self._hello["endpoint_id"]:
            self._hello = None
            self.connect()
        return result

    def send(self, text, images=None, segments=None, **kwargs):
        self.ensure_connected()
        caps = self._hello["capabilities"]
        values = list(segments or ([{"type": "text", "text": text}] if text else []))
        for segment in values:
            kind = segment.get("type")
            if kind in {"image", "record"} and not caps.get(kind):
                raise ConnectorError("远端未声明此媒体能力")
        payload = {
            "conversation_id": str(kwargs.get("conversation_id") or "main"),
            "message_id": str(kwargs.get("message_id") or "msg_" + uuid.uuid4().hex),
            "event_id": str(kwargs.get("event_id") or "evt_" + uuid.uuid4().hex),
            "sequence": 0, "local_sequence": int(kwargs.get("local_sequence") or 0),
            "sender": kwargs.get("sender") or {"profile_id": "me"},
            "created_at": kwargs.get("created_at") or time.time(), "delivery_status": "queued",
            "segments": values, "attachments": kwargs.get("attachments") or [],
            "protocol_version": PROTOCOL_VERSION, "capabilities": caps,
            "endpoint_id": self._hello["endpoint_id"],
        }
        result = require_ok(self.transport.send(payload) if self.transport is not None else
                            request_json(self.config, "/v1/messages", payload))
        if (result.get("accepted") is not True or
                any(result.get(k) != payload[k] for k in ("message_id", "event_id", "conversation_id")) or
                result.get("endpoint_id") != self._hello["endpoint_id"] or
                result.get("delivery_status") not in {
                    "gateway_accepted", "framework_waiting", "framework_unknown", "framework_accepted",
                    "accepted"}):
            raise ConnectorError("网关回执身份或状态不符")
        status = result["delivery_status"]
        if status == "framework_accepted" and not caps.get("delivery_receipts"):
            raise ConnectorError("远端未协商框架接收确认")
        status = "gateway_accepted" if status == "accepted" else status
        return {**result, "endpoint_id": self.endpoint_id, "source_protocol_version": PROTOCOL_VERSION,
                "remote_endpoint_id": self._hello["endpoint_id"], "delivery_status": status,
                "capabilities": caps, "推到": 1}

    def delivery_status(self, message):
        # Read a snapshot; the independent status worker must never reset a handshake.
        hello = self._hello
        if not hello or message.get("remote_endpoint_id") != hello["endpoint_id"]:
            raise ConnectorError("网关尚未连接或消息属于旧端点")
        body = {k: message[k] for k in ("message_id", "event_id", "conversation_id")}
        body["endpoint_id"] = hello["endpoint_id"]
        result = require_ok(request_json(self.config, "/v1/message-status", body))
        if (any(result.get(k) != v for k, v in body.items()) or result.get("delivery_status") not in
                {"gateway_accepted", "framework_waiting", "framework_unknown", "framework_accepted"}):
            raise ConnectorError("投递状态身份或状态不符")
        if result["delivery_status"] == "framework_accepted" and not hello["capabilities"].get("delivery_receipts"):
            raise ConnectorError("未协商框架接收确认")
        return result

    def poll(self):
        self.ensure_connected()
        if self.transport is not None:
            events = self.transport.poll(self._resume_from)
        else:
            events = require_ok(request_json(
                self.config, "/v1/events?resume_from=" + str(self._resume_from))).get("events")
        if not isinstance(events, list) or len(events) > 100:
            raise ConnectorError("网关事件列表无效")
        for event in events:
            if (not isinstance(event, dict) or event.get("protocol_version") != PROTOCOL_VERSION or
                    not all(event.get(k) for k in ("message_id", "event_id", "conversation_id")) or
                    not isinstance(event.get("sequence"), int) or event["sequence"] < 1):
                raise ConnectorError("网关事件身份、版本或序号无效")
        return [{**e, "endpoint_id": self.endpoint_id, "source_endpoint_id": self._hello["endpoint_id"],
                 "source_protocol_version": e.get("source_protocol_version") or PROTOCOL_VERSION,
                 "transport_protocol_version": PROTOCOL_VERSION}
                for e in sorted(events, key=lambda e: e["sequence"])]

    def ack(self, event):
        body = {k: event[k] for k in ("conversation_id", "message_id", "event_id", "sequence")}
        body["endpoint_id"] = self._hello["endpoint_id"]
        reply = require_ok(self.transport.ack(body) if self.transport is not None else
                           request_json(self.config, "/v1/acks", body))
        if any(reply.get(k) != v for k, v in body.items()):
            raise ConnectorError("事件确认身份不符")
        return int(event["sequence"])

    def upload(self, data, mime, name):
        self.ensure_connected()
        if not self._hello["capabilities"].get("media_upload"):
            raise ConnectorError("远端不支持媒体上传")
        kind = "record" if mime.startswith("audio/") else "image"
        limit = min(self._hello["media_limits"][kind], media_limits(self.config.get("media_limits"))[kind])
        if len(data) > limit:
            raise ConnectorError("网关单媒体上限 " + size_label(limit))
        digest = hashlib.sha256(data).hexdigest()
        body = {"sha256": digest, "mime": mime, "name": name,
                "b64": base64.b64encode(data).decode("ascii")}
        reply = require_ok(request_json(self.config, "/v1/media", body))
        if reply.get("sha256") != digest or not reply.get("media_id") or not reply.get("url"):
            raise ConnectorError("媒体上传回执无效")
        return reply

    def download(self, remote_id):
        self.ensure_connected()
        if not self._hello["capabilities"].get("media_download"):
            raise ConnectorError("远端不支持媒体下载")
        reply = require_ok(request_json(self.config, "/v1/media/" + quote(remote_id, safe="")))
        import urllib.request
        from .http import open_request
        route = str(reply.get("url") or "")
        if not route.startswith("/v1/media-content/") or ".." in route:
            raise ConnectorError("远端媒体引用无效")
        req = urllib.request.Request(self.config["base_url"].rstrip("/") + route,
                                     headers={"Authorization": "Bearer " + str(self.config.get("token") or "")})
        kind = "record" if str(reply.get("mime") or "").startswith("audio/") else "image"
        limit = self._hello["media_limits"][kind]
        with open_request(self.config, req) as response:
            data = response.read(limit + 1)
        if len(data) > limit or hashlib.sha256(data).hexdigest() != reply.get("sha256"):
            raise ConnectorError("远端媒体大小或校验值不符")
        return data, reply


class DshLocalConnector(RemoteConnector):
    kind = "dsh_local"

    def __init__(self, config=None, transport=None):
        super().__init__({"allow_insecure_local": True, **(config or {})}, transport)


class RemoteDshConnector(RemoteConnector):
    kind = "dsh_remote"
