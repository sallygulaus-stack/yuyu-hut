"""Connection adapters. Protocol-specific code stops at this package."""

from .base import ConnectorError
from .demo import DemoConnector
from .onebot import OneBotConnector
from .remote import DshLocalConnector, RemoteDshConnector, MemoryRemoteTransport

__all__ = [
    "ConnectorError",
    "DemoConnector",
    "OneBotConnector",
    "DshLocalConnector",
    "RemoteDshConnector",
    "MemoryRemoteTransport",
]
