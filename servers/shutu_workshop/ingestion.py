"""Common JSONL and in-process ingestion boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import IngestResult, IngestValidationError, Trace, TraceEvent
from .redaction import RedactionPolicy
from .storage import M1Database
from .traces import TraceStore


class TraceIngestor:
    def __init__(
        self,
        workspace_or_db: str | Path | M1Database,
        *,
        policy: RedactionPolicy | None = None,
        max_batch_events: int = 1000,
    ) -> None:
        if max_batch_events < 1:
            raise ValueError("max_batch_events must be positive")
        self.policy = policy or RedactionPolicy()
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.traces = TraceStore(self.db, policy=self.policy)
        self.max_batch_events = max_batch_events

    def start(self, trace: Trace) -> Trace:
        return self.traces.start(trace)

    def append(self, event: TraceEvent) -> TraceEvent:
        return self.traces.append(event)

    def batch_append(self, events: Iterable[TraceEvent]) -> IngestResult:
        return self.traces.batch_append(events)

    def close(self, *args: Any, **kwargs: Any) -> Trace:
        return self.traces.close(*args, **kwargs)

    def ingest_records(
        self,
        records: Iterable[dict[str, Any]],
        *,
        source: str = "",
    ) -> IngestResult:
        materialized = list(records)
        if len(materialized) > self.max_batch_events:
            raise IngestValidationError("batch exceeds the configured limit", rejected=len(materialized))
        traces: list[Trace] = []
        events: list[TraceEvent] = []
        for record in materialized:
            if not isinstance(record, dict):
                raise IngestValidationError("each record must be a JSON object", rejected=len(materialized))
            record_type = str(record.get("record_type", record.get("kind", ""))).casefold()
            if record_type in {"trace", "trace.started", "trace_start"} or "trace" in record:
                payload = record.get("trace", record)
                if not isinstance(payload, dict):
                    raise IngestValidationError("trace record must contain an object", rejected=len(materialized))
                try:
                    traces.append(Trace.from_dict(payload))
                except (TypeError, ValueError) as exc:
                    raise IngestValidationError("invalid trace contract", rejected=len(materialized)) from exc
                continue
            if record_type in {"event", "trace_event", "trace.event"} or "event" in record:
                payload = record.get("event", record)
                if not isinstance(payload, dict):
                    raise IngestValidationError("event record must contain an object", rejected=len(materialized))
                try:
                    events.append(TraceEvent.from_dict(payload))
                except (TypeError, ValueError) as exc:
                    raise IngestValidationError("invalid event contract", rejected=len(materialized)) from exc
                continue
            if "trace_id" in record and "seq" in record:
                try:
                    events.append(TraceEvent.from_dict(record))
                except (TypeError, ValueError) as exc:
                    raise IngestValidationError("invalid event contract", rejected=len(materialized)) from exc
                continue
            raise IngestValidationError("record type is required", rejected=len(materialized))
        del source  # source is retained by adapter_id/source fields, never inferred.
        return self.traces.ingest_batch(traces, events)

    def import_jsonl(self, path: str | Path) -> IngestResult:
        records: list[dict[str, Any]] = []
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    if len(line.encode("utf-8")) > self.policy.max_payload_bytes * 2:
                        raise IngestValidationError(
                            f"JSONL line {line_number} exceeds the configured limit",
                            rejected=1,
                        )
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise IngestValidationError(
                            f"invalid JSONL at line {line_number}",
                            rejected=1,
                        ) from exc
                    if not isinstance(value, dict):
                        raise IngestValidationError(
                            f"JSONL line {line_number} must be an object",
                            rejected=1,
                        )
                    records.append(value)
        except UnicodeDecodeError as exc:
            raise IngestValidationError("JSONL input is not UTF-8", rejected=1) from exc
        return self.ingest_records(records, source="jsonl")

    def import_jsonl_text(self, text: str) -> IngestResult:
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            if len(line.encode("utf-8")) > self.policy.max_payload_bytes * 2:
                raise IngestValidationError(
                    f"JSONL line {line_number} exceeds the configured limit",
                    rejected=1,
                )
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IngestValidationError(
                    f"invalid JSONL at line {line_number}",
                    rejected=1,
                ) from exc
            if not isinstance(value, dict):
                raise IngestValidationError("JSONL records must be objects", rejected=1)
            records.append(value)
        return self.ingest_records(records, source="jsonl")
