"""Hermus connector layer.

Connectors translate external systems into world-model observations and expose
explicitly named actions.  Importing this package does not log in, start a
browser, or contact a network service.
"""
from .base import Connector, ConnectorContext, ConnectorStatus
from .filesystem import FilesystemConnector
from .registry import ConnectorRegistry, connector_registry, register_builtin_connectors
from .runtime import RuntimeConnector

__all__ = [
    "Connector",
    "ConnectorContext",
    "ConnectorStatus",
    "ConnectorRegistry",
    "FilesystemConnector",
    "RuntimeConnector",
    "connector_registry",
    "register_builtin_connectors",
]
