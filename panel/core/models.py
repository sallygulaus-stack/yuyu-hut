"""Protocol-neutral data models used inside the client.

Connectors may translate these dictionaries to OneBot, a local DSH endpoint,
or a future remote transport. The UI never needs to know which protocol was
used to produce a message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping

PROTOCOL_VERSION = "dsh-client/1"

CAPABILITIES = {
    "text": True,
    "image": True,
    "record": True,
    "media_ids": True,
    "history_paging": True,
    "profiles": True,
    # No shipped transport has an end-to-end DSH/framework receipt yet.
    # Connectors may advertise this only when they actually negotiate one.
    "delivery_receipts": False,
    "event_resume": True,
    "deduplication": True,
    "idempotent_send": True,
    "media_upload": True,
    "media_download": True,
    "http_poll": True,
    "message_status": True,
}

DELIVERY_STATUSES = {
    "saved",
    "queued",
    "gateway_accepted",
    "framework_waiting",
    "framework_unknown",
    "framework_accepted",
    "failed",
    "unknown",
    # Historical values are retained when reading older JSONL records.
    "accepted",
    "delivered",
}


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


@dataclass(frozen=True)
class Profile:
    profile_id: str
    display_name: str
    avatar: str = ""
    signature: str = ""
    identity: str = ""
    status: str = "offline"
    last_active_at: float | None = None
    role: str = ""
    voice: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    source: str = "local"
    field_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "avatar": self.avatar,
            "signature": self.signature,
            "identity": self.identity,
            "status": self.status,
            "last_active_at": self.last_active_at,
            "role": self.role,
            "voice": _copy(self.voice),
            "capabilities": _copy(self.capabilities),
            "version": self.version,
            "source": self.source,
            "field_sources": dict(self.field_sources),
        }
        return result


@dataclass(frozen=True)
class EndpointInfo:
    endpoint_id: str
    kind: str
    display_name: str
    location: str = "本机"
    state: str = "disconnected"
    protocol_version: str = PROTOCOL_VERSION
    capabilities: dict[str, Any] = field(default_factory=dict)
    secure: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "location": self.location,
            "state": self.state,
            "protocol_version": self.protocol_version,
            "capabilities": _copy(self.capabilities),
            "secure": self.secure,
        }


@dataclass(frozen=True)
class ConnectionStatus:
    phase: str
    configured: bool
    connected: bool
    degraded: bool = False
    connections: int = 0
    label: str = ""
    error: str = ""
    endpoint_id: str = ""
    protocol_version: str = PROTOCOL_VERSION
    capabilities: dict[str, Any] = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)
    endpoint_changed: bool = False
    bound_endpoint_id: str = ""
    observed_endpoint_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "configured": self.configured,
            "connected": self.connected,
            "degraded": self.degraded,
            "connections": self.connections,
            "label": self.label,
            "error": self.error,
            "endpoint_id": self.endpoint_id,
            "protocol_version": self.protocol_version,
            "capabilities": _copy(self.capabilities),
            "checked_at": self.checked_at,
            "endpoint_changed": self.endpoint_changed,
            "bound_endpoint_id": self.bound_endpoint_id,
            "observed_endpoint_id": self.observed_endpoint_id,
            # Compatibility aliases for the existing front end and API tests.
            "通": self.connected,
            "连接": self.connections,
        }


@dataclass(frozen=True)
class UnifiedMessage:
    conversation_id: str
    message_id: str
    event_id: str
    sequence: int
    sender: dict[str, Any]
    created_at: float
    delivery_status: str
    segments: list[dict[str, Any]]
    attachments: list[dict[str, Any]] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=lambda: dict(CAPABILITIES))
    protocol_version: str = PROTOCOL_VERSION
    profile_id: str = ""
    endpoint_id: str = ""
    direction: str = ""
    origin: str = ""
    source_protocol_version: str = ""
    local_sequence: int = 0
    remote_endpoint_id: str = ""
    gateway_accepted: bool = False
    framework_status: str = ""
    delivery_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "event_id": self.event_id,
            "sequence": int(self.sequence),
            "sender": _copy(self.sender),
            "created_at": self.created_at,
            "delivery_status": self.delivery_status,
            "segments": _copy(self.segments),
            "attachments": _copy(self.attachments),
            "capabilities": _copy(self.capabilities),
            "protocol_version": self.protocol_version,
            "profile_id": self.profile_id,
            "endpoint_id": self.endpoint_id,
            "direction": self.direction,
            "origin": self.origin,
            "source_protocol_version": self.source_protocol_version,
            "local_sequence": self.local_sequence,
            "remote_endpoint_id": self.remote_endpoint_id,
            "gateway_accepted": self.gateway_accepted,
            "framework_status": self.framework_status,
            "delivery_detail": self.delivery_detail,
        }


@dataclass(frozen=True)
class ChatEvent:
    message: UnifiedMessage
    event_type: str = "message"
    resume_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = self.message.to_dict()
        result["event_type"] = self.event_type
        if self.resume_token:
            result["resume_token"] = self.resume_token
        return result


def merge_capabilities(*values: Mapping[str, Any] | None) -> dict[str, Any]:
    result = {}
    for value in values:
        if isinstance(value, Mapping):
            result.update({str(key): bool(item) if isinstance(item, bool) else item
                           for key, item in value.items()})
    return result
