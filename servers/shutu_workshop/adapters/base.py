"""Small, host-neutral adapter contracts and bounded command capture."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..ingestion import TraceIngestor
from ..models import IngestResult, Trace, TraceEvent, new_id, utc_now
from ..redaction import RedactionPolicy


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    adapter_version: str
    source_type: str
    host_version: str = "unknown"
    supported_event_types: tuple[str, ...] = ()
    missing_event_types: tuple[str, ...] = ()
    correlation_methods: tuple[str, ...] = ()
    redaction_policy_version: str = RedactionPolicy.version
    health_status: str = "unknown"
    last_error: str = ""
    last_event_at: str = ""
    field_status: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for name in (
            "supported_event_types",
            "missing_event_types",
            "correlation_methods",
        ):
            value[name] = list(value[name])
        return value


@dataclass(frozen=True)
class AdapterHealth:
    status: str
    last_error: str = ""
    last_event_at: str = ""
    accepted: int = 0
    rejected: int = 0
    observed_event_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_event_types"] = list(self.observed_event_types)
        return value


@dataclass(frozen=True)
class CaptureResult:
    exit_code: int | None
    stdout: str
    stderr_summary: str
    timed_out: bool = False
    duration_ms: int = 0


@dataclass(frozen=True)
class AdapterEvent:
    """Normalized event data before it enters the M1 TraceEvent model."""

    type: str
    host_event_id: str
    seq: int
    actor: str = "agent"
    session_id: str = ""
    turn_id: str = ""
    occurred_at: str = field(default_factory=utc_now)
    span_id: str = ""
    parent_span_id: str = ""
    request_id: str = ""
    tool_name: str = ""
    transport: str = ""
    status: str = ""
    error_type: str = ""
    retry_of_event_id: str = ""
    attempt_index: int | None = None
    capability_id: str = ""
    capability_version_id: str = ""
    payload_json: dict[str, Any] = field(default_factory=dict)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    correlation_quality: str = "unknown"

    def to_trace_event(self, trace_id: str, *, adapter_id: str) -> TraceEvent:
        event_key = self.host_event_id or f"{adapter_id}:{self.seq}"
        return TraceEvent(
            trace_id=trace_id,
            seq=self.seq,
            type=self.type,
            actor=self.actor,
            event_id=f"{adapter_id}:{event_key}",
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            occurred_at=self.occurred_at,
            capability_id=self.capability_id,
            capability_version_id=self.capability_version_id,
            adapter_event_id=event_key,
            request_id=self.request_id,
            tool_name=self.tool_name,
            transport=self.transport,
            observation_source="agent_adapter",
            retry_of_event_id=self.retry_of_event_id,
            attempt_index=self.attempt_index,
            status=self.status,
            error_type=self.error_type,
            correlation_quality=self.correlation_quality,
            payload_json=self.payload_json,
            source_metadata=self.source_metadata,
        )


class BaseAdapter:
    """Base class for adapters; it knows M1 only through its public ingest API."""

    adapter_id = "adapter"
    adapter_version = "1"
    source_type = "jsonl"

    def __init__(
        self,
        ingestor: TraceIngestor,
        *,
        workspace_id: str = "local",
        host_version: str = "unknown",
        policy: RedactionPolicy | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.workspace_id = workspace_id
        self.host_version = host_version or "unknown"
        self.policy = policy or RedactionPolicy()
        self._last_error = ""
        self._last_event_at = ""
        self._accepted = 0
        self._rejected = 0
        self._observed_types: set[str] = set()
        self._trace_id = ""

    def descriptor(self) -> AdapterDescriptor:
        return AdapterDescriptor(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            source_type=self.source_type,
            host_version=self.host_version,
            supported_event_types=tuple(self.supported_event_types()),
            missing_event_types=tuple(self.missing_event_types()),
            correlation_methods=("native_event_id", "session_id", "turn_id", "request_id"),
            redaction_policy_version=self.policy.version,
            health_status=self.health().status,
            last_error=self._last_error,
            last_event_at=self._last_event_at,
            field_status=self.field_status(),
        )

    def health(self) -> AdapterHealth:
        status = "error" if self._last_error else ("healthy" if self._accepted else "ready")
        return AdapterHealth(
            status=status,
            last_error=self._last_error,
            last_event_at=self._last_event_at,
            accepted=self._accepted,
            rejected=self._rejected,
            observed_event_types=tuple(sorted(self._observed_types)),
        )

    def supported_event_types(self) -> Sequence[str]:
        return ()

    def missing_event_types(self) -> Sequence[str]:
        return ("skill.discovered", "skill.loaded", "child_agent.started", "mcp.tool_call")

    def field_status(self) -> dict[str, str]:
        return {
            "host_event_id": "observed",
            "session_id": "observed",
            "turn_id": "observed",
            "request_id": "not_observed",
            "tool_identity": "not_observed",
            "transport": "inferred",
            "model": "not_observed",
            "skill_lifecycle": "not_observed",
        }

    def normalize(self, raw: dict[str, Any], index: int, *, trace_id: str) -> AdapterEvent | None:
        raise NotImplementedError

    def ingest_jsonl(
        self,
        text: str,
        *,
        trace_id: str = "",
        close: bool = False,
        exit_code: int | None = 0,
    ) -> IngestResult:
        raw_events: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                self._rejected += 1
                self._last_error = "non-JSON adapter output was ignored"
                continue
            if isinstance(value, dict):
                raw_events.append(value)
            else:
                self._rejected += 1

        trace_id = trace_id or self._stable_trace_id(text)
        normalized: list[AdapterEvent] = []
        normalized_index = 0
        for raw in raw_events:
            record_type = str(raw.get("record_type", raw.get("kind", ""))).casefold()
            if record_type in {"trace", "trace.started", "trace_start"}:
                continue
            if record_type in {"event", "trace_event", "trace.event"} and isinstance(raw.get("event"), dict):
                raw = raw["event"]
            try:
                event = self.normalize(raw, normalized_index, trace_id=trace_id)
            except (TypeError, ValueError, KeyError) as exc:
                self._rejected += 1
                self._last_error = str(exc) or "invalid adapter event"
                continue
            if event is None:
                continue
            normalized_index += 1
            normalized.append(event)
            self._observed_types.add(event.type)
            self._last_event_at = event.occurred_at

        session_id = next((event.session_id for event in normalized if event.session_id), "")
        turn_id = next((event.turn_id for event in normalized if event.turn_id), "")
        trace = Trace(
            trace_id=trace_id,
            workspace_id=self.workspace_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            session_id=session_id,
            turn_id=turn_id,
            source=self.source_type,
            observation_completeness="partial",
            metadata={
                "host_version": self.host_version,
                "field_status": self.field_status(),
            },
        )
        records: list[dict[str, Any]] = [{"record_type": "trace", "trace": trace.to_dict()}]
        records.extend(
            {
                "record_type": "event",
                "event": event.to_trace_event(trace_id, adapter_id=self.adapter_id).to_dict(),
            }
            for event in normalized
        )
        result = self.ingestor.ingest_records(records, source=self.source_type)
        self._accepted += result.accepted
        if result.rejected or result.conflicts:
            self._last_error = "; ".join(result.errors) or "adapter ingest conflict"
        if close:
            status = "ok" if exit_code == 0 and not result.conflicts else "incomplete"
            try:
                self.ingestor.close(
                    trace_id,
                    status,
                    task_result="unknown",
                    observation_completeness="partial",
                )
            except Exception as exc:  # pragma: no cover - database failures are reported by health
                self._last_error = str(exc) or "adapter close failed"
        self._trace_id = trace_id
        return result

    def close(self, *, status: str = "incomplete", trace_id: str = "") -> Trace:
        target = trace_id or self._trace_id
        if not target:
            raise ValueError("no adapter trace has been started")
        return self.ingestor.close(
            target,
            status,
            task_result="unknown",
            observation_completeness="partial",
        )

    def capture(self, command: Sequence[str], *, cwd: str | Path | None = None, timeout: float = 60.0) -> CaptureResult:
        """Run an explicitly supplied command without a shell and ingest its stdout."""

        if not command:
            raise ValueError("capture command is required")
        started = time.perf_counter()
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            stdout = stdout or exc.stdout or ""
            stderr = stderr or exc.stderr or ""
        result = self.ingest_jsonl(stdout, close=True, exit_code=process.returncode)
        del result
        stderr_summary = self.policy.redact(stderr[-8192:]) if stderr else ""
        return CaptureResult(
            exit_code=process.returncode,
            stdout=stdout,
            stderr_summary=str(stderr_summary),
            timed_out=timed_out,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _stable_trace_id(self, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        return f"trace_{self.adapter_id}_{digest}"

    @staticmethod
    def executable_version(executable: str, *args: str) -> str:
        path = shutil.which(executable) or executable
        try:
            completed = subprocess.run(
                [path, *args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        line = (completed.stdout or completed.stderr).strip().splitlines()
        return line[0][:256] if line else "unknown"
