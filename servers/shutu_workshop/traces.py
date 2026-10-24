"""Trace lifecycle, event ordering, and indexed queries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Iterable

from .models import (
    ConflictError,
    IngestResult,
    PayloadLimitError,
    Trace,
    TraceEvent,
    ValidationError,
    utc_now,
)
from .redaction import RedactionPolicy
from .storage import M1Database


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class TraceStore:
    def __init__(
        self,
        workspace_or_db: str | M1Database,
        *,
        policy: RedactionPolicy | None = None,
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.policy = policy or RedactionPolicy()

    def start(self, trace: Trace) -> Trace:
        result = self.ingest_batch([trace], [])
        if result.conflicts:
            raise ConflictError("trace identity conflict")
        return self.get(trace.trace_id) or trace

    def append(self, event: TraceEvent) -> TraceEvent:
        prepared = self._prepare_event(event)
        result = self._apply_batch([], [prepared])
        if result.conflicts:
            raise ConflictError("trace event conflict")
        return self._get_event(prepared.event_id) or prepared

    def batch_append(self, events: Iterable[TraceEvent]) -> IngestResult:
        return self.ingest_batch([], list(events))

    def ingest_batch(
        self,
        traces: Iterable[Trace],
        events: Iterable[TraceEvent],
    ) -> IngestResult:
        prepared_traces = [self._prepare_trace(trace) for trace in traces]
        prepared_events = [self._prepare_event(event) for event in events]
        return self._apply_batch(prepared_traces, prepared_events)

    def close(
        self,
        trace_id: str,
        status: str,
        ended_at: str = "",
        *,
        task_result: str = "unknown",
        observation_completeness: str = "complete",
    ) -> Trace:
        if status not in Trace.STATUSES:
            raise ValidationError("unsupported trace status")
        existing = self.get(trace_id)
        if existing is None:
            raise ValidationError("trace does not exist")
        closed = replace(
            existing,
            status=status,
            ended_at=ended_at or existing.ended_at or utc_now(),
            task_result=task_result or "unknown",
            observation_completeness=observation_completeness or "complete",
        )
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE traces SET ended_at = ?, status = ?,
                    observation_completeness = ?, task_result = ?
                WHERE trace_id = ?
                """,
                (
                    closed.ended_at,
                    closed.status,
                    closed.observation_completeness,
                    closed.task_result,
                    trace_id,
                ),
            )
        return closed

    def get(self, trace_id: str) -> Trace | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        return self._trace_from_row(row) if row else None

    get_trace = get

    def list_traces(
        self,
        *,
        workspace_id: str | None = None,
        adapter_id: str | None = None,
        status: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> list[Trace]:
        query = "SELECT * FROM traces"
        conditions: list[str] = []
        values: list[str] = []
        for name, operator, value in (
            ("workspace_id", "=", workspace_id),
            ("adapter_id", "=", adapter_id),
            ("status", "=", status),
            ("started_at", ">=", started_after),
            ("started_at", "<=", started_before),
        ):
            if value is not None:
                conditions.append(f"{name} {operator} ?")
                values.append(value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at ASC, rowid ASC"
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._trace_from_row(row) for row in rows]

    def list_events(
        self,
        trace_id: str | None = None,
        *,
        occurred_after: str | None = None,
        occurred_before: str | None = None,
        adapter_id: str | None = None,
        capability_id: str | None = None,
        error_type: str | None = None,
        actor: str | None = None,
    ) -> list[TraceEvent]:
        query = "SELECT e.* FROM trace_events e"
        conditions: list[str] = []
        values: list[str] = []
        if adapter_id:
            query += " JOIN traces t ON t.trace_id = e.trace_id"
            conditions.append("t.adapter_id = ?")
            values.append(adapter_id)
        if trace_id is not None:
            conditions.append("e.trace_id = ?")
            values.append(trace_id)
        for name, operator, value in (
            ("e.occurred_at", ">=", occurred_after),
            ("e.occurred_at", "<=", occurred_before),
            ("e.capability_id", "=", capability_id),
            ("e.error_type", "=", error_type),
            ("e.actor", "=", actor),
        ):
            if value is not None:
                conditions.append(f"{name} {operator} ?")
                values.append(value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY e.trace_id, e.seq, e.rowid"
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._event_from_row(row) for row in rows]

    query_events = list_events

    def delete_payloads(self, before: str, *, trace_id: str | None = None) -> int:
        """Delete optional payload bodies while preserving event linkage and status."""

        query = "UPDATE trace_events SET payload_json = ?, source_metadata_json = ? WHERE occurred_at < ?"
        values: list[Any] = ['{"retention":"deleted"}', "{}", before]
        if trace_id is not None:
            query += " AND trace_id = ?"
            values.append(trace_id)
        with self.db.transaction() as connection:
            cursor = connection.execute(query, values)
            return cursor.rowcount

    def search(self, **filters: str) -> list[TraceEvent]:
        return self.list_events(
            trace_id=filters.pop("trace_id", None),
            occurred_after=filters.pop("occurred_after", None),
            occurred_before=filters.pop("occurred_before", None),
            adapter_id=filters.pop("adapter_id", None),
            capability_id=filters.pop("capability_id", None),
            error_type=filters.pop("error_type", None),
            actor=filters.pop("actor", None),
        )

    def _prepare_trace(self, trace: Trace) -> Trace:
        if not trace.trace_id or not trace.workspace_id or not trace.adapter_id:
            raise ValidationError("trace_id, workspace_id and adapter_id are required")
        if trace.status not in Trace.STATUSES:
            raise ValidationError("unsupported trace status")
        return replace(
            trace,
            redaction_policy_version=trace.redaction_policy_version or self.policy.version,
            token_summary=self.policy.redact_metadata(trace.token_summary),
            cost_summary=self.policy.redact_metadata(trace.cost_summary),
            metadata=self.policy.redact_metadata(trace.metadata),
            task_result=trace.task_result or "unknown",
            observation_completeness=trace.observation_completeness or "complete",
        )

    def _prepare_event(self, event: TraceEvent) -> TraceEvent:
        if not event.trace_id or not isinstance(event.seq, int) or isinstance(event.seq, bool) or event.seq < 0:
            raise ValidationError("trace_id and a non-negative integer seq are required")
        if not event.type:
            raise ValidationError("event type is required")
        payload = self.policy.redact_payload(event.payload_json)
        source_metadata = self.policy.redact_metadata(event.source_metadata)
        if not isinstance(payload, dict):
            raise ValidationError("event payload_json must be an object")
        prepared = replace(event, payload_json=payload, source_metadata=source_metadata)
        if prepared.duration_ms is not None and prepared.duration_ms < 0:
            raise ValidationError("duration_ms cannot be negative")
        if prepared.attempt_index is not None and prepared.attempt_index < 0:
            raise ValidationError("attempt_index cannot be negative")
        if not prepared.event_id:
            identity = self._event_identity(prepared)
            prefix = "evt_src" if prepared.adapter_event_id else "evt_auto"
            prepared = replace(
                prepared,
                event_id=f"{prefix}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}",
            )
        encoded = _canonical(prepared.to_dict()).encode("utf-8")
        if len(encoded) > self.policy.max_payload_bytes * 2:
            raise PayloadLimitError("event exceeds the configured limit")
        return prepared

    @staticmethod
    def _event_identity(event: TraceEvent) -> str:
        values = event.to_dict()
        values.pop("event_id", None)
        return _canonical(values)

    @staticmethod
    def _trace_identity(trace: Trace) -> dict[str, Any]:
        return {
            "trace_id": trace.trace_id,
            "workspace_id": trace.workspace_id,
            "adapter_id": trace.adapter_id,
            "parent_trace_id": trace.parent_trace_id,
            "session_id": trace.session_id,
            "turn_id": trace.turn_id,
            "started_at": trace.started_at,
            "environment_fingerprint": trace.environment_fingerprint,
            "source": trace.source,
        }

    def _apply_batch(
        self,
        traces: list[Trace],
        events: list[TraceEvent],
    ) -> IngestResult:
        accepted = 0
        duplicates = 0
        errors: list[str] = []
        new_traces: list[Trace] = []
        new_events: list[TraceEvent] = []
        known_trace_ids: set[str] = set()
        known_event_ids: dict[str, str] = {}
        known_seq: dict[tuple[str, int], str] = {}
        known_sources: dict[tuple[str, str], str] = {}

        with self.db.transaction() as connection:
            for trace in traces:
                if trace.trace_id in known_trace_ids:
                    duplicates += 1
                    continue
                known_trace_ids.add(trace.trace_id)
                row = connection.execute(
                    "SELECT * FROM traces WHERE trace_id = ?",
                    (trace.trace_id,),
                ).fetchone()
                if row:
                    existing = self._trace_from_row(row)
                    if self._trace_identity(existing) != self._trace_identity(trace):
                        errors.append("trace identity conflict")
                    else:
                        duplicates += 1
                    continue
                new_traces.append(trace)

            existing_trace_ids = {
                row[0] for row in connection.execute("SELECT trace_id FROM traces")
            }
            existing_trace_ids.update(trace.trace_id for trace in new_traces)
            for event in events:
                if event.trace_id not in existing_trace_ids:
                    raise ValidationError("event references an unknown trace")
                digest = self._event_digest(event)
                row = connection.execute(
                    "SELECT * FROM trace_events WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                seq_row = connection.execute(
                    "SELECT * FROM trace_events WHERE trace_id = ? AND seq = ?",
                    (event.trace_id, event.seq),
                ).fetchone()
                source_row = None
                if event.adapter_event_id:
                    source_row = connection.execute(
                        """
                        SELECT * FROM trace_events
                        WHERE trace_id = ? AND adapter_event_id = ?
                        """,
                        (event.trace_id, event.adapter_event_id),
                    ).fetchone()
                conflict = False
                for candidate in (row, seq_row, source_row):
                    if candidate is None:
                        continue
                    if candidate["payload_digest"] == digest and self._event_row_digest(candidate) == digest:
                        if event.event_id not in known_event_ids and (event.trace_id, event.seq) not in known_seq:
                            duplicates += 1
                        conflict = True
                        break
                    errors.append("event identity conflict")
                    conflict = True
                    break
                if conflict:
                    continue
                if event.event_id in known_event_ids:
                    if known_event_ids[event.event_id] == digest:
                        duplicates += 1
                    else:
                        errors.append("event identity conflict")
                    continue
                if (event.trace_id, event.seq) in known_seq:
                    if known_seq[(event.trace_id, event.seq)] == digest:
                        duplicates += 1
                    else:
                        errors.append("event sequence conflict")
                    continue
                if event.adapter_event_id and (event.trace_id, event.adapter_event_id) in known_sources:
                    if known_sources[(event.trace_id, event.adapter_event_id)] == digest:
                        duplicates += 1
                    else:
                        errors.append("source event conflict")
                    continue
                if event.capability_version_id:
                    version_row = connection.execute(
                        "SELECT capability_id FROM capability_versions WHERE version_id = ?",
                        (event.capability_version_id,),
                    ).fetchone()
                    if version_row is None:
                        raise ValidationError("event references an unknown capability version")
                    if event.capability_id and version_row[0] != event.capability_id:
                        raise ValidationError("event capability and version do not match")
                if event.capability_id:
                    capability_row = connection.execute(
                        "SELECT 1 FROM capabilities WHERE capability_id = ?",
                        (event.capability_id,),
                    ).fetchone()
                    if capability_row is None:
                        raise ValidationError("event references an unknown capability")
                known_event_ids[event.event_id] = digest
                known_seq[(event.trace_id, event.seq)] = digest
                if event.adapter_event_id:
                    known_sources[(event.trace_id, event.adapter_event_id)] = digest
                new_events.append(event)

            if errors:
                return IngestResult(conflicts=len(errors), errors=tuple(errors))

            for trace in new_traces:
                connection.execute(
                    """
                    INSERT INTO traces(
                        trace_id, workspace_id, adapter_id, parent_trace_id,
                        session_id, turn_id, started_at, ended_at, status,
                        environment_fingerprint, source, observation_completeness,
                        redaction_policy_version, token_summary_json,
                        cost_summary_json, task_result, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace.trace_id,
                        trace.workspace_id,
                        trace.adapter_id,
                        trace.parent_trace_id,
                        trace.session_id,
                        trace.turn_id,
                        trace.started_at,
                        trace.ended_at,
                        trace.status,
                        trace.environment_fingerprint,
                        trace.source,
                        trace.observation_completeness,
                        trace.redaction_policy_version,
                        _canonical(trace.token_summary),
                        _canonical(trace.cost_summary),
                        trace.task_result,
                        _canonical(trace.metadata),
                    ),
                )
            for event in new_events:
                connection.execute(
                    """
                    INSERT INTO trace_events(
                        event_id, trace_id, seq, span_id, parent_span_id, type,
                        actor, occurred_at, duration_ms, capability_id,
                        capability_version_id, adapter_event_id, retry_of_event_id,
                        attempt_index, status, error_type, correlation_quality,
                        payload_json, source_metadata_json, payload_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.trace_id,
                        event.seq,
                        event.span_id,
                        event.parent_span_id,
                        event.type,
                        event.actor,
                        event.occurred_at,
                        event.duration_ms,
                        event.capability_id,
                        event.capability_version_id,
                        event.adapter_event_id,
                        event.retry_of_event_id,
                        event.attempt_index,
                        event.status,
                        event.error_type,
                        event.correlation_quality,
                        _canonical(event.payload_json),
                        _canonical(event.source_metadata),
                        self._event_digest(event),
                    ),
                )
            accepted = len(new_traces) + len(new_events)
        return IngestResult(accepted=accepted, duplicates=duplicates)

    @staticmethod
    def _event_digest(event: TraceEvent) -> str:
        values = event.to_dict()
        values.pop("event_id", None)
        return hashlib.sha256(_canonical(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _event_row_digest(row: Any) -> str:
        values = {
            "trace_id": row["trace_id"],
            "seq": row["seq"],
            "type": row["type"],
            "actor": row["actor"],
            "span_id": row["span_id"],
            "parent_span_id": row["parent_span_id"],
            "occurred_at": row["occurred_at"],
            "duration_ms": row["duration_ms"],
            "capability_id": row["capability_id"],
            "capability_version_id": row["capability_version_id"],
            "adapter_event_id": row["adapter_event_id"],
            "retry_of_event_id": row["retry_of_event_id"],
            "attempt_index": row["attempt_index"],
            "status": row["status"],
            "error_type": row["error_type"],
            "correlation_quality": row["correlation_quality"],
            "payload_json": json.loads(row["payload_json"]),
            "source_metadata": json.loads(row["source_metadata_json"]),
        }
        return hashlib.sha256(_canonical(values).encode("utf-8")).hexdigest()

    def _get_event(self, event_id: str) -> TraceEvent | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM trace_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._event_from_row(row) if row else None

    @staticmethod
    def _trace_from_row(row: Any) -> Trace:
        return Trace(
            trace_id=row["trace_id"],
            workspace_id=row["workspace_id"],
            adapter_id=row["adapter_id"],
            parent_trace_id=row["parent_trace_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            environment_fingerprint=row["environment_fingerprint"],
            source=row["source"],
            observation_completeness=row["observation_completeness"],
            redaction_policy_version=row["redaction_policy_version"],
            token_summary=json.loads(row["token_summary_json"]),
            cost_summary=json.loads(row["cost_summary_json"]),
            task_result=row["task_result"],
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _event_from_row(row: Any) -> TraceEvent:
        return TraceEvent(
            event_id=row["event_id"],
            trace_id=row["trace_id"],
            seq=row["seq"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            type=row["type"],
            actor=row["actor"],
            occurred_at=row["occurred_at"],
            duration_ms=row["duration_ms"],
            capability_id=row["capability_id"],
            capability_version_id=row["capability_version_id"],
            adapter_event_id=row["adapter_event_id"],
            retry_of_event_id=row["retry_of_event_id"],
            attempt_index=row["attempt_index"],
            status=row["status"],
            error_type=row["error_type"],
            correlation_quality=row["correlation_quality"],
            payload_json=json.loads(row["payload_json"]),
            source_metadata=json.loads(row["source_metadata_json"]),
        )
