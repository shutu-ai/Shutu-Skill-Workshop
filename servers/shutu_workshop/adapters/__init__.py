"""Host-specific adapters that feed the host-neutral M1 trace contract."""

from .base import (
    AdapterDescriptor,
    AdapterEvent,
    AdapterHealth,
    BaseAdapter,
    CaptureResult,
)
from .real_agent import CodexRealTaskRunner, RealAgentRunEvidence
__all__ = [
    "AdapterDescriptor",
    "AdapterEvent",
    "AdapterHealth",
    "BaseAdapter",
    "CaptureResult",
    "CodexRealTaskRunner",
    "RealAgentRunEvidence",
    "CodexAdapter",
]


def __getattr__(name: str):
    if name == "CodexAdapter":
        from .codex import CodexAdapter

        return CodexAdapter
    raise AttributeError(name)
