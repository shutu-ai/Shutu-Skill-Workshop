"""Small, host-neutral adapter contracts and bounded command capture."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field, replace
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
    accepted: int = 0
    rejected: int = 0
    incomplete_lines: int = 0
    output_truncated: bool = False
    trace_id: str = ""
    errors: tuple[str, ...] = ()


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
        result = self._ingest_raw_events(raw_events, trace_id=trace_id)
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

    def ingest_event(
        self,
        event: dict[str, Any],
        *,
        trace_id: str,
        close: bool = False,
        exit_code: int | None = 0,
    ) -> IngestResult:
        """Ingest one bounded host event while retaining a stable trace scope."""

        if not trace_id:
            raise ValueError("trace_id is required for incremental ingestion")
        text = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        return self.ingest_jsonl(text, trace_id=trace_id, close=close, exit_code=exit_code)

    def _ingest_raw_events(self, raw_events: list[dict[str, Any]], *, trace_id: str) -> IngestResult:
        normalized: list[AdapterEvent] = []
        next_seq = self.ingestor.traces.next_event_seq(trace_id)
        local_sources: dict[str, int] = {}
        normalized_index = next_seq
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
            if event.host_event_id.startswith("line-"):
                fingerprint = hashlib.sha256(
                    json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()[:24]
                event = replace(event, host_event_id=f"raw-{fingerprint}")
            existing = self.ingestor.traces.find_event_by_source(trace_id, event.host_event_id)
            if existing is not None:
                event = replace(event, seq=existing.seq)
            elif event.host_event_id in local_sources:
                event = replace(event, seq=local_sources[event.host_event_id])
            else:
                event = replace(event, seq=next_seq)
                local_sources[event.host_event_id] = next_seq
                next_seq += 1
            source_metadata = dict(event.source_metadata)
            source_metadata["host_event_id"] = event.host_event_id
            source_metadata.setdefault(
                "correlation_scope",
                {
                    "workspace_id": self.workspace_id,
                    "run_id": trace_id,
                    "agent_session_id": event.session_id,
                    "transport": event.transport or self.source_type,
                    "request_id": event.request_id,
                },
            )
            normalized.append(replace(event, source_metadata=source_metadata))
            self._observed_types.add(event.type)
            self._last_event_at = event.occurred_at

        existing_trace = self.ingestor.traces.get(trace_id)
        if existing_trace:
            # Trace identity is immutable; later session/turn evidence remains
            # on the event instead of rewriting an already-started trace.
            session_id = existing_trace.session_id
            turn_id = existing_trace.turn_id
        else:
            session_id = next((event.session_id for event in normalized if event.session_id), "")
            turn_id = next((event.turn_id for event in normalized if event.turn_id), "")
        trace = Trace(
            trace_id=trace_id,
            workspace_id=self.workspace_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            session_id=session_id,
            turn_id=turn_id,
            started_at=existing_trace.started_at if existing_trace else utc_now(),
            source=self.source_type,
            observation_completeness="partial",
            metadata={
                "host_version": self.host_version,
                "field_status": self.field_status(),
                "correlation_scope": {
                    "workspace_id": self.workspace_id,
                    "run_id": trace_id,
                    "agent_session_id": session_id,
                    "transport": self.source_type,
                },
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

    def capture(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout: float = 60.0,
    ) -> CaptureResult:
        """Compatibility wrapper using the bounded streaming capture path."""

        return self.capture_stream(command, cwd=cwd, timeout=timeout)

    def capture_stream(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        timeout: float = 60.0,
        trace_id: str = "",
        max_line_bytes: int = 256 * 1024,
        batch_size: int = 50,
        queue_size: int = 100,
        max_stderr_bytes: int = 16 * 1024,
        max_stdout_bytes: int = 64 * 1024,
    ) -> CaptureResult:
        """Capture JSONL with bounded queues, line buffers and diagnostics."""

        if not command:
            raise ValueError("capture command is required")
        if timeout <= 0 or max_line_bytes < 256 or batch_size < 1 or queue_size < 1:
            raise ValueError("invalid capture limits")
        if max_stderr_bytes < 256 or max_stdout_bytes < 256:
            raise ValueError("capture output limits are too small")

        started = time.perf_counter()
        active_trace_id = trace_id or new_id(f"trace-{self.adapter_id}")
        process = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        messages: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=queue_size)
        stdout_done = threading.Event()
        stderr_chunks = bytearray()
        stderr_lock = threading.Lock()
        errors: list[str] = []
        stdout_sample = bytearray()
        stdout_truncated = False
        incomplete_lines = 0
        accepted = 0
        rejected = 0
        timed_out = False
        batch: list[str] = []

        def put(kind: str, value: bytes = b"") -> None:
            messages.put((kind, value))

        def read_stdout() -> None:
            try:
                assert process.stdout is not None
                while True:
                    line = process.stdout.readline(max_line_bytes + 1)
                    if not line:
                        break
                    if len(line) > max_line_bytes:
                        while line and not line.endswith(b"\n"):
                            line = process.stdout.readline(max_line_bytes + 1)
                        put("oversize")
                        continue
                    if not line.endswith(b"\n"):
                        put("partial", line)
                        continue
                    put("line", line)
            except (OSError, ValueError) as exc:
                put("reader_error", type(exc).__name__.encode("ascii", "replace"))
            finally:
                put("eof")
                stdout_done.set()

        def read_stderr() -> None:
            try:
                assert process.stderr is not None
                while True:
                    chunk = process.stderr.read(4096)
                    if not chunk:
                        break
                    with stderr_lock:
                        remaining = max_stderr_bytes - len(stderr_chunks)
                        if remaining > 0:
                            stderr_chunks.extend(chunk[:remaining])
            except (OSError, ValueError) as exc:
                errors.append(type(exc).__name__)

        stdout_thread = threading.Thread(target=read_stdout, name="adapter-stdout", daemon=True)
        stderr_thread = threading.Thread(target=read_stderr, name="adapter-stderr", daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        def flush(close: bool = False, exit_code: int | None = None) -> None:
            nonlocal accepted, rejected
            if not batch:
                return
            text = "".join(batch)
            batch.clear()
            try:
                result = self.ingest_jsonl(
                    text,
                    trace_id=active_trace_id,
                    close=close,
                    exit_code=exit_code,
                )
            except Exception as exc:
                rejected += len(text.splitlines())
                errors.append(type(exc).__name__)
                self._last_error = type(exc).__name__
                return
            accepted += result.accepted
            rejected += result.rejected + result.conflicts
            if result.errors:
                errors.extend(result.errors)

        try:
            while not stdout_done.is_set() or not messages.empty():
                if not timed_out and time.perf_counter() - started > timeout:
                    timed_out = True
                    errors.append("timeout")
                    process.terminate()
                try:
                    kind, value = messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind == "line":
                    try:
                        decoded = value.decode("utf-8")
                        json.loads(decoded)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        rejected += 1
                        errors.append("invalid_json")
                        continue
                    if len(stdout_sample) < max_stdout_bytes:
                        allowed = max_stdout_bytes - len(stdout_sample)
                        stdout_sample.extend(value[:allowed])
                        stdout_truncated = stdout_truncated or len(value) > allowed
                    batch.append(decoded)
                    if len(batch) >= batch_size:
                        flush()
                elif kind == "partial":
                    incomplete_lines += 1
                    errors.append("partial_line")
                elif kind == "oversize":
                    rejected += 1
                    errors.append("line_too_long")
                elif kind == "reader_error":
                    errors.append(value.decode("ascii", "replace") or "stdout_reader_error")
                elif kind == "eof":
                    stdout_done.set()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                errors.append("process_killed")
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

        exit_code = process.returncode
        if batch:
            flush(close=True, exit_code=exit_code)
        elif self._trace_id == active_trace_id:
            try:
                self.close(status="incomplete" if timed_out or exit_code else "ok", trace_id=active_trace_id)
            except Exception as exc:
                errors.append(type(exc).__name__)
        stderr_summary = self.policy.redact(bytes(stderr_chunks).decode("utf-8", "replace"))
        stdout_text = self.policy.redact(bytes(stdout_sample).decode("utf-8", "replace"))
        if errors:
            self._last_error = errors[-1]
        return CaptureResult(
            exit_code=exit_code,
            stdout=str(stdout_text),
            stderr_summary=str(stderr_summary),
            timed_out=timed_out,
            duration_ms=round((time.perf_counter() - started) * 1000),
            accepted=accepted,
            rejected=rejected,
            incomplete_lines=incomplete_lines,
            output_truncated=stdout_truncated,
            trace_id=active_trace_id,
            errors=tuple(dict.fromkeys(errors)),
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
