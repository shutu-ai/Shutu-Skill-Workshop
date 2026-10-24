"""Host-neutral M1 data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, ClassVar
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _known_values(cls: type[Any], payload: dict[str, Any]) -> dict[str, Any]:
    names = {item.name for item in fields(cls)}
    return {key: value for key, value in payload.items() if key in names}


@dataclass(frozen=True)
class Capability:
    capability_id: str
    kind: str
    adapter_id: str
    locator: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_from: str = ""
    discovered_at: str = field(default_factory=utc_now)

    KINDS: ClassVar[frozenset[str]] = frozenset(
        {"skill", "mcp_server", "mcp_tool", "agent_policy"}
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Capability":
        return cls(**_known_values(cls, payload))


@dataclass(frozen=True)
class CapabilityVersion:
    capability_id: str
    source: str
    version_id: str = field(default_factory=lambda: new_id("capver"))
    parent_version_id: str = ""
    content_hash: str = ""
    schema_hash: str = ""
    provider_version: str = "unknown"
    observed_at: str = field(default_factory=utc_now)
    environment_fingerprint: str = ""
    snapshot_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CapabilityVersion":
        return cls(**_known_values(cls, payload))


@dataclass(frozen=True)
class Trace:
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    workspace_id: str = ""
    adapter_id: str = ""
    parent_trace_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    started_at: str = field(default_factory=utc_now)
    ended_at: str = ""
    status: str = "running"
    environment_fingerprint: str = ""
    source: str = ""
    observation_completeness: str = "complete"
    redaction_policy_version: str = "m1-default-v1"
    token_summary: dict[str, Any] = field(default_factory=dict)
    cost_summary: dict[str, Any] = field(default_factory=dict)
    task_result: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"running", "ok", "failed", "incomplete", "cancelled", "unknown"}
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Trace":
        return cls(**_known_values(cls, payload))


@dataclass(frozen=True)
class TraceEvent:
    trace_id: str
    seq: int
    type: str
    actor: str = "unknown"
    event_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    occurred_at: str = field(default_factory=utc_now)
    duration_ms: int | None = None
    capability_id: str = ""
    capability_version_id: str = ""
    adapter_event_id: str = ""
    retry_of_event_id: str = ""
    attempt_index: int | None = None
    status: str = ""
    error_type: str = ""
    correlation_quality: str = "observed"
    payload_json: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def payload(self) -> dict[str, Any]:
        """Short alias for callers that prefer ``payload`` to the wire name."""

        return self.payload_json

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TraceEvent":
        values = dict(payload)
        if "payload_json" not in values and "payload" in values:
            values["payload_json"] = values.pop("payload")
        return cls(**_known_values(cls, values))


@dataclass(frozen=True)
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    conflicts: int = 0
    rejected: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.conflicts and not self.rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "rejected": self.rejected,
            "errors": list(self.errors),
        }


class WorkshopDataError(ValueError):
    """Base class for invalid or conflicting M1 data."""


class ValidationError(WorkshopDataError):
    pass


class ConflictError(WorkshopDataError):
    pass


class IngestValidationError(ValidationError):
    def __init__(self, message: str, *, rejected: int = 0) -> None:
        super().__init__(message)
        self.rejected = rejected


class PayloadLimitError(IngestValidationError):
    pass
