"""OneBot v11 adapter. OneBot objects are translated at this boundary."""

from __future__ import annotations

import time
from typing import Any

from core.models import ConnectionStatus
from .base import Connector, ConnectorError
from .http import request_json
from media_limits import media_limits, LEGACY_ONEBOT_LIMITS

ONEBOT_CAPS = {"text": True, "image": True, "record": True, "media_ids": True,
               "profiles": True, "idempotent_send": True, "deduplication": True,
               "delivery_receipts": False, "onebot_v11": True, "message_status": True}

class OneBotConnector(Connector):
    kind = "onebot"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.last_error = ""
        self.last_checked = 0.0
        self.last_state: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        self.last_checked = time.time()
        endpoint_id = str(self.config.get("endpoint_id") or "onebot-local")
        if not self.config.get("enabled"):
            return ConnectionStatus(
                phase="disabled", configured=False, connected=False,
                label="OneBot 未启用", endpoint_id=endpoint_id,
                capabilities={}, checked_at=self.last_checked,
            ).to_dict()
        try:
            result = request_json(self.config, "/__state")
            self.last_state = result
            connected = int(result.get("connections", result.get("连接数", 0)) or 0)
            phase = "connected" if connected > 0 else "disconnected"
            label = "OneBot 已连接" if connected else "等待机器人框架连接"
            return ConnectionStatus(
                phase=phase, configured=True, connected=connected > 0,
                connections=connected, label=label, endpoint_id=endpoint_id,
                protocol_version=str(result.get("protocolVersion") or "onebot-v11"),
                capabilities=ONEBOT_CAPS,
                checked_at=self.last_checked,
            ).to_dict() | {
                "self_id": result.get("selfId"),
                "peer_id": result.get("peerId"),
                "profiles": {"onebot": {
                    "me": {"identity": str(result.get("peerId") or ""),
                           "display_name": result.get("peerName") or ""},
                    "peer": {"identity": str(result.get("selfId") or ""),
                             "display_name": result.get("selfName") or ""}}},
                "version": result.get("version") or result.get("appVersion") or "",
                "media_limits": media_limits(result.get("media_limits"), LEGACY_ONEBOT_LIMITS),
            }
        except Exception as exc:
            self.last_error = str(exc)[:300]
            return ConnectionStatus(
                phase="error", configured=True, connected=False,
                label="OneBot 连接错误", error=self.last_error,
                endpoint_id=endpoint_id, capabilities=ONEBOT_CAPS,
                checked_at=self.last_checked,
            ).to_dict()

    def send(self, text: str, images: list[dict[str, Any]] | None = None,
             segments: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        payload_images = list(images or [])
        if not payload_images and segments:
            for segment in segments:
                if segment.get("type") == "image" and segment.get("b64"):
                    payload_images.append({
                        "b64": segment["b64"],
                        "name": segment.get("name") or segment.get("名称") or "image.png",
                    })
        import uuid
        message_id = str(kwargs.get("message_id") or "msg_" + uuid.uuid4().hex)
        event_id = str(kwargs.get("event_id") or "evt_" + uuid.uuid4().hex)
        result = request_json(self.config, "/__inject", {
            "text": text, "segments": list(segments or []),
            "图": [] if segments else payload_images, "message_id": message_id,
            "event_id": event_id, "conversation_id": kwargs.get("conversation_id") or "main"})
        if (result.get("ok") is not True or result.get("accepted") is not True or
                result.get("message_id") != message_id or result.get("event_id") != event_id):
            raise ConnectorError("OneBot 未接收消息")
        return {
            "ok": True,
            "推到": result.get("pushed", result.get("推到", 0)),
            "delivery_status": result.get("delivery_status") or
                               ("framework_unknown" if result.get("pushed", 0) else "framework_waiting"),
            "endpoint_id": str(self.config.get("endpoint_id") or "onebot-local"),
            "protocol_version": "onebot-v11",
            "source_protocol_version": "onebot-v11",
            "message_id": message_id, "event_id": event_id,
            "capabilities": ONEBOT_CAPS,
        }

    def delivery_status(self, message):
        result = request_json(self.config, "/__delivery", {
            k: message[k] for k in ("message_id", "event_id", "conversation_id")})
        if (result.get("ok") is not True or result.get("accepted") is not True or
                any(result.get(k) != message[k] for k in ("message_id", "event_id")) or
                result.get("delivery_status") not in {"framework_waiting", "framework_unknown"}):
            raise ConnectorError("OneBot 投递状态无效")
        return result
