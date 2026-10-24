"""Offline, repeatable post-release observation over already-ingested traces."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..models import Trace, TraceEvent, new_id, utc_now
from ..storage import M1Database
from ..traces import TraceStore


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ObservationWindow:
    before_start: str
    before_end: str
    after_start: str
    after_end: str
    min_samples: int = 2
    task_category: str = ""
    source: str = ""
    environment_fingerprint: str = ""
    agent_id: str = ""
    runner_id: str = ""
    capability_version_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationResult:
    observation_id: str
    release_id: str
    window_hash: str
    conclusion: str
    summary: dict[str, Any]
    window: dict[str, Any]
    status: str
    created_at: str

    @property
    def signal(self) -> str:
        return str(self.summary.get("signal") or self.conclusion)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReleaseObservationService:
    def __init__(self, workspace_or_db: str | Path | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.traces = TraceStore(self.db)

    def observe(
        self,
        release_id: str,
        window: ObservationWindow | dict[str, Any],
        *,
        min_samples: int | None = None,
    ) -> ObservationResult:
        value = window if isinstance(window, ObservationWindow) else ObservationWindow(**dict(window))
        if min_samples is not None:
            value = ObservationWindow(**{**value.to_dict(), "min_samples": max(0, int(min_samples))})
        link = self._release(release_id)
        if link is None:
            raise ValueError("release does not exist")
        window_hash = _digest({"release_id": release_id, "window": value.to_dict()})
        source_fingerprint = ""
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM release_observation_runs WHERE release_id = ? AND window_hash = ?",
                (release_id, window_hash),
            ).fetchone()
        try:
            before = self._collect(link, value, "before")
            after = self._collect(link, value, "after")
            conclusion, signal, reasons = self._conclude(before, after, value)
            source_fingerprint = _digest({"before": before.get("source_fingerprint"), "after": after.get("source_fingerprint")})
            summary = {
                "release_id": release_id,
                "release_version_id": link["m0_version_id"],
                "candidate_artifact_hash": link["candidate_artifact_hash"],
                "baseline_artifact_hash": link["baseline_artifact_hash"],
                "before": before,
                "after": after,
                "signal": signal,
                "data_gaps": reasons,
                "source_fingerprint": source_fingerprint,
                "token_count": None,
                "cost": None,
                "aggregation": "offline_sqlite_read",
            }
            status = "completed"
        except (OSError, ValueError, RuntimeError) as exc:
            conclusion = "MONITORING_ERROR"
            summary = {"release_id": release_id, "error": f"{type(exc).__name__}: {exc}", "token_count": None, "cost": None}
            status = "failed"
        now = utc_now()
        observation_id = row["observation_id"] if row is not None else new_id("observation")
        result = ObservationResult(observation_id, release_id, window_hash, conclusion, summary, value.to_dict(), status, now)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO release_observation_runs(
                    observation_id, release_id, window_hash, window_json,
                    conclusion, status, summary_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(release_id, window_hash) DO UPDATE SET
                    conclusion = excluded.conclusion,
                    status = excluded.status,
                    summary_json = excluded.summary_json,
                    updated_at = excluded.updated_at
                """,
                (
                    result.observation_id,
                    result.release_id,
                    result.window_hash,
                    _canonical(result.window),
                    result.conclusion,
                    result.status,
                    _canonical(result.summary),
                    result.created_at,
                    result.created_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM release_observation_runs WHERE release_id = ? AND window_hash = ?",
                (release_id, window_hash),
            ).fetchone()
        if row is not None and row["observation_id"] == observation_id and row["summary_json"]:
            current = self._from_row(row)
            if current.summary.get("source_fingerprint") == source_fingerprint and current.created_at != now:
                return current
        return self._from_row(row)

    def record_simulated_trace(
        self,
        release_id: str,
        *,
        phase: str,
        artifact_hash: str,
        task_result: str,
        started_at: str,
        task_category: str = "isolated-skill",
        environment_fingerprint: str = "isolated-file-v1",
        runner_id: str = "shutu.file-backed-isolated-runner",
        error_type: str = "",
    ) -> Trace:
        """Create an explicitly labelled local trace for the M5 demonstration only."""

        trace = Trace(
            trace_id=new_id("trace"),
            workspace_id="m5-isolated",
            adapter_id=runner_id,
            adapter_version="1",
            started_at=started_at,
            status="ok" if task_result in {"ok", "success"} else ("failed" if task_result in {"failed", "error"} else "unknown"),
            environment_fingerprint=environment_fingerprint,
            source="SIMULATED_TRACE",
            observation_completeness="complete",
            task_result=task_result,
            metadata={
                "release_id": release_id,
                "release_phase": phase,
                "artifact_hash": artifact_hash,
                "target_scope": "isolated_workspace",
                "task_category": task_category,
                "runner_id": runner_id,
                "simulated": True,
            },
        )
        self.traces.start(trace)
        event = TraceEvent(
            trace_id=trace.trace_id,
            seq=0,
            type="skill.execution",
            actor="isolated_runner",
            occurred_at=started_at,
            duration_ms=1,
            status="error" if error_type else "ok",
            error_type=error_type,
            observation_source="SIMULATED_TRACE",
            correlation_quality="verified",
            payload_json={"release_id": release_id, "artifact_hash": artifact_hash},
        )
        self.traces.append(event)
        self.traces.close(trace.trace_id, trace.status, task_result=task_result, observation_completeness="complete")
        return self.traces.get(trace.trace_id) or trace

    def _collect(self, link: Any, window: ObservationWindow, phase: str) -> dict[str, Any]:
        if phase == "before":
            start, end, expected_hash = window.before_start, window.before_end, link["baseline_artifact_hash"]
        else:
            start, end, expected_hash = window.after_start, window.after_end, link["candidate_artifact_hash"]
        traces = self.traces.list_traces(started_after=start, started_before=end)
        included: list[Trace] = []
        excluded: dict[str, int] = {}
        for trace in traces:
            metadata = trace.metadata if isinstance(trace.metadata, dict) else {}
            if metadata.get("release_id") != link["release_id"]:
                excluded["release_id_mismatch"] = excluded.get("release_id_mismatch", 0) + 1
                continue
            if metadata.get("release_phase") != phase:
                excluded["phase_mismatch"] = excluded.get("phase_mismatch", 0) + 1
                continue
            if metadata.get("artifact_hash") != expected_hash:
                excluded["artifact_hash_mismatch"] = excluded.get("artifact_hash_mismatch", 0) + 1
                continue
            if trace.source != "SIMULATED_TRACE" and not metadata.get("verified_bridge"):
                excluded["unverified_cross_source_bridge"] = excluded.get("unverified_cross_source_bridge", 0) + 1
                continue
            if window.environment_fingerprint and trace.environment_fingerprint != window.environment_fingerprint:
                excluded["environment_mismatch"] = excluded.get("environment_mismatch", 0) + 1
                continue
            if window.task_category and metadata.get("task_category") != window.task_category:
                excluded["task_category_mismatch"] = excluded.get("task_category_mismatch", 0) + 1
                continue
            if window.source and trace.source != window.source:
                excluded["source_mismatch"] = excluded.get("source_mismatch", 0) + 1
                continue
            if window.runner_id and metadata.get("runner_id") != window.runner_id:
                excluded["runner_mismatch"] = excluded.get("runner_mismatch", 0) + 1
                continue
            included.append(trace)
        success = sum(trace.task_result in {"ok", "success"} for trace in included)
        failed = sum(trace.task_result in {"failed", "error"} for trace in included)
        unknown = len(included) - success - failed
        events = [event for trace in included for event in self.traces.list_events(trace.trace_id)]
        durations = [int(event.duration_ms) for event in events if event.duration_ms is not None]
        retries = sum(1 for event in events if event.retry_of_event_id or (event.attempt_index or 0) > 0)
        issue_occurrences = self._occurrences([trace.trace_id for trace in included])
        return {
            "phase": phase,
            "window": {"start": start, "end": end},
            "trace_ids": [trace.trace_id for trace in included],
            "sample_count": len(included),
            "success_count": success,
            "failure_count": failed,
            "unknown_count": unknown,
            "success_rate": (success / len(included)) if included else None,
            "issue_occurrence_count": issue_occurrences,
            "tool_call_count": len(events),
            "retry_count": retries,
            "duration_ms": self._distribution(durations),
            "observation_completeness": self._completeness(included),
            "excluded": excluded,
            "task_categories": sorted({str((trace.metadata or {}).get("task_category") or "") for trace in included}),
            "environments": sorted({trace.environment_fingerprint for trace in included}),
            "runners": sorted({str((trace.metadata or {}).get("runner_id") or trace.adapter_id) for trace in included}),
            "source_fingerprint": _digest(
                {
                    "traces": [
                        {
                            "trace_id": trace.trace_id,
                            "status": trace.status,
                            "ended_at": trace.ended_at,
                            "task_result": trace.task_result,
                            "metadata": trace.metadata,
                            "events": [event.event_id for event in self.traces.list_events(trace.trace_id)],
                        }
                        for trace in included
                    ],
                    "excluded": excluded,
                }
            ),
        }

    def _conclude(self, before: dict[str, Any], after: dict[str, Any], window: ObservationWindow) -> tuple[str, str, list[str]]:
        gaps: list[str] = []
        if before["sample_count"] < window.min_samples:
            gaps.append("before_sample_count")
        if after["sample_count"] < window.min_samples:
            gaps.append("after_sample_count")
        if not before["sample_count"] or not after["sample_count"]:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", gaps
        if before["task_categories"] != after["task_categories"] or before["environments"] != after["environments"]:
            gaps.append("task_or_environment_composition_changed")
            return "NOT_COMPARABLE", "INCONCLUSIVE", gaps
        if gaps:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", gaps
        if after["failure_count"] > before["failure_count"]:
            return "REGRESSION_SIGNAL", "REGRESSION_SIGNAL", gaps
        if after["success_rate"] > before["success_rate"]:
            return "IMPROVEMENT_SIGNAL", "SUPPORTED_IMPROVEMENT", gaps
        return "NO_CLEAR_CHANGE", "NO_CLEAR_CHANGE", gaps

    @staticmethod
    def _distribution(values: list[int]) -> dict[str, Any]:
        if not values:
            return {"count": 0, "median_ms": None, "p95_ms": None}
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * 0.95)))
        return {"count": len(ordered), "median_ms": statistics.median(ordered), "p95_ms": ordered[index]}

    @staticmethod
    def _completeness(traces: Iterable[Trace]) -> str:
        values = [trace.observation_completeness for trace in traces]
        if not values:
            return "unknown"
        if all(value == "complete" for value in values):
            return "complete"
        if any(value == "unknown" for value in values):
            return "partial"
        return "partial"

    def _occurrences(self, trace_ids: list[str]) -> int:
        if not trace_ids:
            return 0
        placeholders = ",".join("?" for _ in trace_ids)
        with self.db.connection() as connection:
            row = connection.execute(f"SELECT count(*) FROM issue_occurrences WHERE trace_id IN ({placeholders})", trace_ids).fetchone()
        return int(row[0]) if row else 0

    def _release(self, release_id: str) -> Any:
        with self.db.connection() as connection:
            return connection.execute("SELECT * FROM release_links WHERE release_id = ?", (release_id,)).fetchone()

    @staticmethod
    def _from_row(row: Any) -> ObservationResult:
        return ObservationResult(
            observation_id=row["observation_id"],
            release_id=row["release_id"],
            window_hash=row["window_hash"],
            conclusion=row["conclusion"],
            summary=json.loads(row["summary_json"]),
            window=json.loads(row["window_json"]),
            status=row["status"],
            created_at=row["created_at"],
        )


__all__ = ["ObservationResult", "ObservationWindow", "ReleaseObservationService"]
