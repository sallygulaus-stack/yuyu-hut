"""Reusable client-core models shared by every connector."""

from .models import (
    CAPABILITIES,
    PROTOCOL_VERSION,
    ChatEvent,
    ConnectionStatus,
    EndpointInfo,
    Profile,
    UnifiedMessage,
)
from .dedupe import EventDeduper

__all__ = [
    "CAPABILITIES",
    "PROTOCOL_VERSION",
    "ChatEvent",
    "ConnectionStatus",
    "EndpointInfo",
    "Profile",
    "UnifiedMessage",
    "EventDeduper",
]
