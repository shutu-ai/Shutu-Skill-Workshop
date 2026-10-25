"""Import bounded Codex hook summaries into the existing Workshop Core."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..ingestion import TraceIngestor
from ..models import Trace, TraceEvent, utc_now

MAX_EVENTS_PER_FLUSH = 100
MAX_TRACKED_KEYS = 5000

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - exercised on Unix CI only
    import fcntl


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


@dataclass(frozen=True)
class HookImportResult:
    status: str
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    event_count: int = 0
    trace_ids: tuple[str, ...] = ()
    error: str = ""
    source: str = "CODEX_HOOK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "event_count": self.event_count,
            "trace_ids": list(self.trace_ids),
            "error": self.error,
            "source": self.source,
        }


class HookSpoolImporter:
    """Read summary-only hook events and commit them idempotently to Core.

    The spool is read while sharing the hook writer's lock.  The checkpoint is
    an event-key set, not a byte offset, so atomic replacement and retention do
    not cause replay or loss after a restart.
    """

    adapter_id = "codex-hook"
    adapter_version = "1"
    source_type = "codex-hook-jsonl"

    def __init__(self, workspace: str | Path, plugin_data: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.plugin_data = Path(plugin_data).expanduser().resolve()
        self.spool_path = self.plugin_data / "hook_events.jsonl"
        self.state_path = self.plugin_data / "hook_import_state.json"
        self.lock_path = self.plugin_data / "hook.lock"
        self.ingestor = TraceIngestor(self.workspace)

    @property
    def database_path(self) -> Path:
        return self.workspace / ".skill_workshop" / "state.db"

    def flush(self, *, max_events: int = MAX_EVENTS_PER_FLUSH) -> HookImportResult:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if not self.spool_path.is_file():
            return HookImportResult(status="NO_DATA")
        try:
            with self._lock():
                state = self._load_state()
                stored_keys = [str(item) for item in state.get("imported_event_keys", [])]
                imported_keys = set(stored_keys)
                records, selected_keys, trace_ids, new_trace_count = self._records(imported_keys, max_events)
                if not records:
                    return HookImportResult(status="NO_DATA", trace_ids=tuple(sorted(trace_ids)))
                result = self.ingestor.ingest_records(records, source=self.source_type)
                if result.conflicts:
                    return HookImportResult(
                        status="OBSERVATION_GAP",
                        accepted=max(0, result.accepted - new_trace_count),
                        duplicates=result.duplicates,
                        rejected=result.rejected,
                        event_count=len(selected_keys),
                        trace_ids=tuple(sorted(trace_ids)),
                        error="; ".join(result.errors)[:200],
                    )
                event_accepted = max(0, result.accepted - new_trace_count)
                imported_keys.update(selected_keys)
                ordered_keys = [key for key in stored_keys if key in imported_keys]
                ordered_keys.extend(key for key in selected_keys if key not in ordered_keys)
                state.update(
                    {
                        "version": 1,
                        "source": "CODEX_HOOK",
                        "imported_event_keys": ordered_keys[-MAX_TRACKED_KEYS:],
                        "imported": int(state.get("imported", 0)) + event_accepted,
                        "duplicates": int(state.get("duplicates", 0)) + result.duplicates,
                        "rejected": int(state.get("rejected", 0)) + result.rejected,
                        "last_import_at": _now(),
                        "last_event_count": len(selected_keys),
                    }
                )
                self._save_state(state)
                return HookImportResult(
                    status="PASS" if not result.rejected else "OBSERVATION_GAP",
                    accepted=event_accepted,
                    duplicates=result.duplicates,
                    rejected=result.rejected,
                    event_count=len(selected_keys),
                    trace_ids=tuple(sorted(trace_ids)),
                    error="; ".join(result.errors)[:200],
                )
        except (OSError, ValueError, TypeError) as exc:
            return HookImportResult(status="OBSERVATION_GAP", error=type(exc).__name__)

    def _records(self, imported_keys: set[str], max_events: int) -> tuple[list[dict[str, Any]], list[str], set[str], int]:
        events: list[dict[str, Any]] = []
        selected_keys: list[str] = []
        trace_ids: set[str] = set()
        next_sequences: dict[str, int] = {}
        trace_sessions: dict[str, str] = {}
        for line in self.spool_path.read_text(encoding="utf-8").splitlines():
            if len(events) >= max_events:
                break
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            key = _text(value.get("event_id")) or _hash(value)
            if key in imported_keys:
                continue
            session_id = _text(value.get("session_id_hash"))
            trace_id = "hook-session-" + (session_id or key)
            if trace_id not in next_sequences:
                next_sequences[trace_id] = self.ingestor.traces.next_event_seq(trace_id)
            event, trace = self._event(value, key, seq=next_sequences[trace_id])
            next_sequences[trace_id] += 1
            trace_sessions[trace_id] = session_id
            events.append(event)
            trace_ids.add(trace.trace_id)
            selected_keys.append(key)

        traces: dict[str, Trace] = {}
        for event in events:
            trace_id = event["event"]["trace_id"]
            if trace_id in traces:
                continue
            existing = self.ingestor.traces.get(trace_id)
            if existing is None:
                traces[trace_id] = self._trace(trace_id, trace_sessions.get(trace_id, ""))
        return (
            [{"record_type": "trace", "trace": trace.to_dict()} for trace in traces.values()] + events,
            selected_keys,
            trace_ids,
            len(traces),
        )

    def _trace(self, trace_id: str, session_id: str) -> Trace:
        return Trace(
            trace_id=trace_id,
            workspace_id=str(self.workspace),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            session_id=session_id,
            started_at=utc_now(),
            source=self.source_type,
            observation_completeness="partial",
            metadata={
                "observation_source": "CODEX_HOOK",
                "source_evidence_tier": "REAL_OBSERVED",
                "hook_summary_only": True,
            },
        )

    def _event(self, value: dict[str, Any], key: str, *, seq: int) -> tuple[dict[str, Any], Trace]:
        session_id = _text(value.get("session_id_hash"))
        trace_id = "hook-session-" + (session_id or key)
        hook_name = _text(value.get("hook_event_name")) or "unknown"
        tool_name = _text(value.get("tool_name"))
        result_status = _text(value.get("result_status"))
        is_mcp = tool_name.casefold().startswith(("mcp__", "mcp.", "workshop_"))
        is_error = result_status == "error"
        event_type = "mcp.tool_result" if is_mcp else "tool.result"
        if hook_name.casefold() != "posttooluse":
            event_type = "task.started" if hook_name.casefold() == "sessionstart" else "host.hook." + hook_name.casefold()
        source_scope = {
            "workspace_id": str(self.workspace),
            "run_id": trace_id,
            "agent_session_id": session_id,
            "transport": "codex-cli-hook",
            "mcp_server": self._mcp_server(tool_name),
            "request_id": _text(value.get("request_id_hash")),
        }
        event = TraceEvent(
            trace_id=trace_id,
            seq=seq,
            type=event_type,
            actor="tool" if hook_name.casefold() == "posttooluse" else "agent",
            event_id="codex-hook:" + key,
            occurred_at=_text(value.get("captured_at")) or utc_now(),
            adapter_event_id=key,
            request_id=_text(value.get("request_id_hash")),
            tool_name=tool_name,
            transport="codex-cli-hook",
            observation_source="CODEX_HOOK",
            status=result_status or ("ok" if hook_name.casefold() == "posttooluse" else "observed"),
            error_type="mcp.error" if is_mcp and is_error else ("hook.tool_error" if is_error else ""),
            correlation_quality="exact" if key else "unknown",
            payload_json={
                "hook_event_name": hook_name,
                "argument_keys": value.get("argument_keys", []),
                "result_status": result_status,
                "isError": bool(is_mcp and is_error),
                "permission_mode": _text(value.get("permission_mode")),
                "redaction": "summary_only",
            },
            source_metadata={
                "host_event_id": key,
                "observation_source": "CODEX_HOOK",
                "source_evidence_tier": "REAL_OBSERVED",
                "session_id_hash": session_id,
                "turn_id_hash": _text(value.get("turn_id_hash")),
                "cwd_hash": _text(value.get("cwd_hash")),
                "correlation_scope": source_scope,
            },
        )
        return {"record_type": "event", "event": event.to_dict()}, self._trace(trace_id, session_id)

    @staticmethod
    def _mcp_server(tool_name: str) -> str:
        parts = tool_name.split("__")
        return parts[1] if len(parts) >= 3 and parts[0].casefold() == "mcp" else ""

    def _load_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    def _save_state(self, value: dict[str, Any]) -> None:
        self.plugin_data.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.plugin_data, delete=False) as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
            temporary = Path(handle.name)
        os.replace(temporary, self.state_path)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.plugin_data.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exercised on Unix CI only
                fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on Unix CI only
                    fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()


__all__ = ["HookImportResult", "HookSpoolImporter"]
