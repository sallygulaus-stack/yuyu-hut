"""Offline synthetic connector."""

from __future__ import annotations

from typing import Any

from core.models import CAPABILITIES, ConnectionStatus
from .base import Connector


class DemoConnector(Connector):
    kind = "demo"

    def status(self) -> dict[str, Any]:
        return ConnectionStatus(
            phase="connected",
            configured=True,
            connected=True,
            connections=1,
            label="演示模式",
            endpoint_id="demo",
            capabilities=dict(CAPABILITIES),
        ).to_dict()

    def send(self, text: str, images: list[dict[str, Any]] | None = None,
             segments: list[dict[str, Any]] | None = None, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "reply": "收到图片了。" if images or any((x.get("type") == "image" for x in segments or []))
            else "演示回复：" + text,
            "推到": 1,
            "delivery_status": "delivered",
            "endpoint_id": "demo",
            "protocol_version": "dsh-client/1",
        }
