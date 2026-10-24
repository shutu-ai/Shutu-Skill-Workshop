"""Transparent MCP observers for explicit local test connections."""

from .common import CorrelationScope, McpTraceRecorder, ObserverReport
from .stdio import StdioObserver
from .streamable_http import HTTPProxyResult, StreamableHTTPObserver

__all__ = [
    "HTTPProxyResult",
    "CorrelationScope",
    "McpTraceRecorder",
    "ObserverReport",
    "create_relay_server",
    "StdioObserver",
    "StreamableHTTPObserver",
]


def __getattr__(name: str):
    if name == "create_relay_server":
        from .relay import create_relay_server

        return create_relay_server
    raise AttributeError(name)
