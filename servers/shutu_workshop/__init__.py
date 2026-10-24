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
    "McpTraceRecorder",
    "ObserverReport",
    "StdioObserver",
    "StreamableHTTPObserver",
    "ValidationError",
    "content_hash",
    "create_ingest_server",
    "serve_in_thread",
]


def __getattr__(name: str):
    if name == "CodexAdapter":
        from .adapters.codex import CodexAdapter

        return CodexAdapter
    raise AttributeError(name)
