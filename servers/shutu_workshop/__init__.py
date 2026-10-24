"""M1 host-neutral Capability/Trace foundation for Shutu Skill Workshop."""

from .capabilities import CapabilityRegistry, content_hash
from .http import create_ingest_server, serve_in_thread
from .ingestion import TraceIngestor
from .models import (
    Capability,
    CapabilityVersion,
    ConflictError,
    IngestResult,
    IngestValidationError,
    PayloadLimitError,
    Trace,
    TraceEvent,
    ValidationError,
)
from .redaction import RedactionPolicy
from .storage import M1Database
from .traces import TraceStore
from .adapters import AdapterDescriptor, AdapterEvent, AdapterHealth, BaseAdapter, CaptureResult
from .adapters.opencode import OpenCodeAdapter
from .capture import Checkpoint, CheckpointedJSONL
from .observers.mcp import (
    CorrelationScope,
    HTTPProxyResult,
    McpTraceRecorder,
    ObserverReport,
    StdioObserver,
    StreamableHTTPObserver,
)

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "CapabilityVersion",
    "AdapterDescriptor",
    "AdapterEvent",
    "AdapterHealth",
    "BaseAdapter",
    "CaptureResult",
    "CodexAdapter",
    "OpenCodeAdapter",
    "Checkpoint",
    "CheckpointedJSONL",
    "ConflictError",
    "IngestResult",
    "IngestValidationError",
    "M1Database",
    "PayloadLimitError",
    "RedactionPolicy",
    "Trace",
    "TraceEvent",
    "TraceIngestor",
    "TraceStore",
    "HTTPProxyResult",
    "CorrelationScope",
    "McpTraceRecorder",
    "ObserverReport",
    "StdioObserver",
    "StreamableHTTPObserver",
    "create_relay_server",
    "ValidationError",
    "content_hash",
    "create_ingest_server",
    "serve_in_thread",
]


def __getattr__(name: str):
    if name == "CodexAdapter":
        from .adapters.codex import CodexAdapter

        return CodexAdapter
    if name == "create_relay_server":
        from .observers.mcp.relay import create_relay_server

        return create_relay_server
    raise AttributeError(name)
