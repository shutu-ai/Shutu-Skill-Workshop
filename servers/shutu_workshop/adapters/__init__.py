"""Host-specific adapters that feed the host-neutral M1 trace contract."""

from .base import (
    AdapterDescriptor,
    AdapterEvent,
    AdapterHealth,
    BaseAdapter,
    CaptureResult,
)
from .real_agent import ArtifactActivationRecord, CodexRealTaskRunner, RealAgentRunEvidence, codex_preflight
__all__ = [
    "AdapterDescriptor",
    "AdapterEvent",
    "AdapterHealth",
    "BaseAdapter",
    "CaptureResult",
    "CodexRealTaskRunner",
    "RealAgentRunEvidence",
    "ArtifactActivationRecord",
    "codex_preflight",
    "CodexAdapter",
]


def __getattr__(name: str):
    if name == "CodexAdapter":
        from .codex import CodexAdapter

        return CodexAdapter
    raise AttributeError(name)
