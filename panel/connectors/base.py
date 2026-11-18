"""Small connector contract used by the client runtime."""

from __future__ import annotations

from typing import Any

from core.models import CAPABILITIES, ConnectionStatus


class ConnectorError(RuntimeError):
    pass


class Connector:
    kind = "unknown"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def status(self) -> dict[str, Any]:
        return ConnectionStatus(
            phase="disconnected",
            configured=bool(self.config.get("enabled")),
            connected=False,
            endpoint_id=str(self.config.get("endpoint_id") or self.kind),
            capabilities={},
        ).to_dict()

    def send(self, text: str, images: list[dict[str, Any]] | None = None,
             segments: list[dict[str, Any]] | None = None, **_kwargs: Any) -> dict[str, Any]:
        raise ConnectorError("连接器没有实现发送")
