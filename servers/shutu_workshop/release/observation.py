"""Offline, repeatable post-release observation over already-ingested traces."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from ..models import Trace, TraceEvent, new_id, utc_now
from ..storage import M1Database
from ..traces import TraceStore


_EVIDENCE_TIERS = frozenset(
    {
        "REAL_AGENT_OBSERVED",
        "ISOLATED_ARTIFACT_EXECUTED",
        "FIXTURE_EXECUTED",
        "SIMULATED_TRACE",
        "UNKNOWN",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenComparisonManifest:
    """Immutable identity used to select before/after traces."""

    release_id: str = ""
    workspace_id: str = ""
    target_skill_name: str = ""
    baseline_artifact_hash: str = ""
    baseline_capability_version_id: str = ""
    candidate_artifact_hash: str = ""
    candidate_capability_version_id: str = ""
    task_suite_id: str = ""
    task_category: str = ""
    agent_id: str = ""
    provider_model: str = ""
    environment_fingerprint: str = ""
    runner_id: str = ""
    runner_version: str = ""
    capture_window_start: str = ""
    capture_window_end: str = ""
    comparison_strategy: str = "paired_preferred"
    min_samples: int = 2
    unknown_max_ratio: float = 0.25
    experiment_id: str = ""
    correlation_id: str = ""
    correlation_quality: str = "unknown"
    manifest_id: str = field(default_factory=lambda: new_id("comparison"))
    frozen_at: str = field(default_factory=utc_now)
    manifest_hash: str = ""
    legacy_binding: bool = False

    def __post_init__(self) -> None:
        if not self.manifest_hash:
            object.__setattr__(self, "manifest_hash", _digest(self._unsigned()))
        elif self.manifest_hash != _digest(self._unsigned()):
            raise ValueError("comparison manifest hash mismatch")

    def _unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("manifest_hash", None)
        return value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FrozenComparisonManifest":
        names = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in value.items() if key in names})


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
    comparison_manifest_id: str = ""
    comparison_manifest_hash: str = ""
    comparison_manifest: dict[str, Any] = field(default_factory=dict)
    experiment_id: str = ""
    pair_id: str = ""
    source_evidence_tier: str = ""
    unknown_max_ratio: float = 0.25

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

    def freeze_manifest(
        self,
        release_id: str,
        manifest: FrozenComparisonManifest | dict[str, Any],
    ) -> FrozenComparisonManifest:
        link = self._release(release_id)
        if link is None:
            raise ValueError("release does not exist")
        value = manifest if isinstance(manifest, FrozenComparisonManifest) else FrozenComparisonManifest.from_dict(manifest)
        value_dict = value.to_dict()
        value_dict.update(
            {
                "release_id": release_id,
                "baseline_artifact_hash": value.baseline_artifact_hash or str(link["baseline_artifact_hash"]),
                "candidate_artifact_hash": value.candidate_artifact_hash or str(link["candidate_artifact_hash"]),
                "target_skill_name": value.target_skill_name or str(link["target_skill_name"]),
                "manifest_hash": "",
            }
        )
        value = FrozenComparisonManifest.from_dict(value_dict)
        if value.baseline_artifact_hash != str(link["baseline_artifact_hash"]):
            raise ValueError("comparison manifest baseline hash differs from release")
        if value.candidate_artifact_hash != str(link["candidate_artifact_hash"]):
            raise ValueError("comparison manifest candidate hash differs from release")
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM comparison_manifests WHERE manifest_id = ?",
                (value.manifest_id,),
            ).fetchone()
            if existing is not None:
                if existing["manifest_hash"] != value.manifest_hash:
                    raise ValueError("comparison manifest identity is already frozen")
                return FrozenComparisonManifest.from_dict(json.loads(existing["manifest_json"]))
            connection.execute(
                """
                INSERT INTO comparison_manifests(
                    manifest_id, release_id, manifest_hash, manifest_json,
                    created_at, updated_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    value.manifest_id,
                    release_id,
                    value.manifest_hash,
                    _canonical(value.to_dict()),
                    value.frozen_at,
                    value.frozen_at,
                ),
            )
        return value

    def get_manifest(self, manifest_id: str) -> FrozenComparisonManifest | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT manifest_json FROM comparison_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
        return FrozenComparisonManifest.from_dict(json.loads(row[0])) if row else None

    def get(self, observation_id: str) -> ObservationResult | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM release_observation_runs WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        return self._from_row(row) if row else None

    def observe(
        self,
        release_id: str,
        window: ObservationWindow | dict[str, Any],
        *,
        min_samples: int | None = None,
        comparison_manifest: FrozenComparisonManifest | dict[str, Any] | None = None,
    ) -> ObservationResult:
        value = window if isinstance(window, ObservationWindow) else ObservationWindow(**dict(window))
        if min_samples is not None:
            value = replace(value, min_samples=max(0, int(min_samples)))
        link = self._release(release_id)
        if link is None:
            raise ValueError("release does not exist")
        manifest = self._resolve_manifest(release_id, link, value, comparison_manifest)
        window_hash = _digest(
            {
                "release_id": release_id,
                "window": value.to_dict(),
                "comparison_manifest_hash": manifest.manifest_hash,
            }
        )
        source_fingerprint = ""
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM release_observation_runs WHERE release_id = ? AND window_hash = ?",
                (release_id, window_hash),
            ).fetchone()
        try:
            before = self._collect(link, value, "before", manifest)
            after = self._collect(link, value, "after", manifest)
            conclusion, signal, reasons = self._conclude(before, after, value, manifest)
            source_fingerprint = _digest(
                {
                    "before": before.get("source_fingerprint"),
                    "after": after.get("source_fingerprint"),
                    "manifest_hash": manifest.manifest_hash,
                }
            )
            summary = {
                "release_id": release_id,
                "release_version_id": link["m0_version_id"],
                "candidate_artifact_hash": link["candidate_artifact_hash"],
                "baseline_artifact_hash": link["baseline_artifact_hash"],
                "comparison_manifest": manifest.to_dict(),
                "comparison_manifest_hash": manifest.manifest_hash,
                "before": before,
                "after": after,
                "signal": signal,
                "data_gaps": reasons,
                "false_exact_join_count": 0,
                "source_fingerprint": source_fingerprint,
                "token_count": None,
                "cost": None,
                "aggregation": "offline_sqlite_read",
            }
            status = "completed"
        except (OSError, ValueError, RuntimeError) as exc:
            conclusion = "MONITORING_ERROR"
            summary = {
                "release_id": release_id,
                "comparison_manifest_hash": manifest.manifest_hash,
                "error": f"{type(exc).__name__}: {exc}",
                "token_count": None,
                "cost": None,
            }
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
        task_suite_id: str = "",
        error_type: str = "",
        comparison_manifest_hash: str = "",
        case_id: str = "",
        pair_id: str = "",
        experiment_id: str = "",
    ) -> Trace:
        """Create an explicitly labelled local trace for demonstration only."""

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
                "task_suite_id": task_suite_id,
                "source_evidence_tier": "SIMULATED_TRACE",
                "comparison_manifest_hash": comparison_manifest_hash,
                "case_id": case_id,
                "pair_id": pair_id,
                "experiment_id": experiment_id,
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
            error_type=error_type,
            status="error" if error_type else "ok",
            observation_source="SIMULATED_TRACE",
            correlation_quality="verified",
            payload_json={"artifact_hash": artifact_hash, "case_id": case_id},
        )
        self.traces.append(event)
        self.traces.close(trace.trace_id, trace.status, task_result=task_result, observation_completeness="complete")
        return self.traces.get(trace.trace_id) or trace

    def _resolve_manifest(
        self,
        release_id: str,
        link: Any,
        window: ObservationWindow,
        explicit: FrozenComparisonManifest | dict[str, Any] | None,
    ) -> FrozenComparisonManifest:
        value: FrozenComparisonManifest | None = None
        if explicit is not None:
            value = explicit if isinstance(explicit, FrozenComparisonManifest) else FrozenComparisonManifest.from_dict(explicit)
        elif window.comparison_manifest:
            value = FrozenComparisonManifest.from_dict(window.comparison_manifest)
        elif window.comparison_manifest_id:
            value = self.get_manifest(window.comparison_manifest_id)
            if value is None:
                raise ValueError("comparison manifest does not exist")
        if value is not None:
            return self.freeze_manifest(release_id, value)
        # M5 compatibility: old local demonstration traces were explicitly release-bound.
        return FrozenComparisonManifest(
            release_id=release_id,
            target_skill_name=str(link["target_skill_name"]),
            baseline_artifact_hash=str(link["baseline_artifact_hash"]),
            candidate_artifact_hash=str(link["candidate_artifact_hash"]),
            task_category=window.task_category,
            agent_id=window.agent_id,
            environment_fingerprint=window.environment_fingerprint,
            runner_id=window.runner_id,
            min_samples=window.min_samples,
            unknown_max_ratio=window.unknown_max_ratio,
            experiment_id=window.experiment_id,
            correlation_quality="legacy_release_id",
            manifest_id=f"legacy-comparison-{release_id}",
            frozen_at="legacy",
            legacy_binding=True,
        )

    def _collect(self, link: Any, window: ObservationWindow, phase: str, manifest: FrozenComparisonManifest) -> dict[str, Any]:
        if phase == "before":
            start, end, expected_hash = window.before_start, window.before_end, manifest.baseline_artifact_hash
        else:
            start, end, expected_hash = window.after_start, window.after_end, manifest.candidate_artifact_hash
        traces = self.traces.list_traces(started_after=start, started_before=end)
        included: list[Trace] = []
        included_events: dict[str, list[TraceEvent]] = {}
        excluded: dict[str, int] = {}
        evidence_tiers: list[str] = []
        for trace in traces:
            metadata = trace.metadata if isinstance(trace.metadata, dict) else {}
            events = self.traces.list_events(trace.trace_id)
            reason = self._binding_reason(trace, events, metadata, link, window, phase, expected_hash, manifest)
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue
            tier = self._evidence_tier(trace, metadata)
            if tier not in _EVIDENCE_TIERS:
                tier = "UNKNOWN"
            if window.source_evidence_tier and tier != window.source_evidence_tier:
                excluded["source_evidence_tier_mismatch"] = excluded.get("source_evidence_tier_mismatch", 0) + 1
                continue
            if window.source and trace.source != window.source:
                excluded["source_mismatch"] = excluded.get("source_mismatch", 0) + 1
                continue
            included.append(trace)
            included_events[trace.trace_id] = events
            evidence_tiers.append(tier)
        success = sum(trace.task_result in {"ok", "success"} for trace in included)
        failed = sum(trace.task_result in {"failed", "error"} for trace in included)
        unknown = len(included) - success - failed
        observed = success + failed
        events = [event for values in included_events.values() for event in values]
        durations = [int(event.duration_ms) for event in events if event.duration_ms is not None]
        retries = sum(1 for event in events if event.retry_of_event_id or (event.attempt_index or 0) > 0)
        tool_calls = sum(event.type in {"mcp.tool_call", "tool.call", "tool.before", "tool.started"} for event in events)
        trace_digests = {
            trace.trace_id: _digest(
                {
                    "trace": trace.to_dict(),
                    "events": [event.to_dict() for event in included_events[trace.trace_id]],
                }
            )
            for trace in included
        }
        return {
            "phase": phase,
            "window": {"start": start, "end": end},
            "trace_ids": [trace.trace_id for trace in included],
            "trace_digests": trace_digests,
            "sample_count": len(included),
            "n_total": len(included),
            "n_observed": observed,
            "n_success": success,
            "n_failed": failed,
            "n_unknown": unknown,
            "success_count": success,
            "failure_count": failed,
            "unknown_count": unknown,
            "success_rate": (success / observed) if observed else None,
            "failure_rate": (failed / observed) if observed else None,
            "unknown_rate": (unknown / len(included)) if included else None,
            "issue_occurrence_count": self._occurrences([trace.trace_id for trace in included]),
            "tool_call_count": tool_calls,
            "event_count": len(events),
            "retry_count": retries,
            "duration_ms": self._distribution(durations),
            "observation_completeness": self._completeness(included),
            "excluded": excluded,
            "source_evidence_tiers": sorted(set(evidence_tiers)),
            "task_categories": sorted({str((trace.metadata or {}).get("task_category") or "") for trace in included}),
            "environments": sorted({trace.environment_fingerprint for trace in included}),
            "runners": sorted({str((trace.metadata or {}).get("runner_id") or trace.adapter_id) for trace in included}),
            "agent_ids": sorted({str((trace.metadata or {}).get("agent_id") or "") for trace in included}),
            "provider_models": sorted({str((trace.metadata or {}).get("provider_model") or "") for trace in included}),
            "case_ids": sorted({str((trace.metadata or {}).get("case_id") or "") for trace in included if (trace.metadata or {}).get("case_id")}),
            "pair_ids": sorted({str((trace.metadata or {}).get("pair_id") or "") for trace in included if (trace.metadata or {}).get("pair_id")}),
            "source_fingerprint": _digest(
                {
                    "traces": [
                        {
                            "trace_id": trace.trace_id,
                            "trace_digest": trace_digests[trace.trace_id],
                            "events": [event.to_dict() for event in included_events[trace.trace_id]],
                        }
                        for trace in included
                    ],
                    "excluded": excluded,
                    "evidence_tiers": evidence_tiers,
                }
            ),
        }

    def _binding_reason(
        self,
        trace: Trace,
        events: list[TraceEvent],
        metadata: dict[str, Any],
        link: Any,
        window: ObservationWindow,
        phase: str,
        expected_hash: str,
        manifest: FrozenComparisonManifest,
    ) -> str:
        release_id = str(metadata.get("release_id") or "")
        if phase == "after" and release_id != str(link["release_id"]):
            return "release_id_mismatch"
        if phase == "before" and release_id and release_id != str(link["release_id"]):
            return "release_id_mismatch"
        if str(metadata.get("release_phase") or "") and metadata.get("release_phase") != phase:
            return "phase_mismatch"
        if metadata.get("artifact_hash") != expected_hash:
            return "artifact_hash_mismatch"
        if window.environment_fingerprint and trace.environment_fingerprint != window.environment_fingerprint:
            return "environment_mismatch"
        task_category = manifest.task_category or window.task_category
        if task_category and metadata.get("task_category") != task_category:
            return "task_category_mismatch"
        runner_id = manifest.runner_id or window.runner_id
        if runner_id and metadata.get("runner_id") != runner_id:
            return "runner_mismatch"
        if manifest.agent_id and metadata.get("agent_id") != manifest.agent_id:
            return "agent_mismatch"
        if manifest.provider_model and metadata.get("provider_model") != manifest.provider_model:
            return "provider_model_mismatch"
        if manifest.task_suite_id and metadata.get("task_suite_id") != manifest.task_suite_id:
            return "task_suite_mismatch"
        if manifest.experiment_id and metadata.get("experiment_id") != manifest.experiment_id:
            return "experiment_mismatch"
        if manifest.legacy_binding:
            if release_id != str(link["release_id"]):
                return "release_id_mismatch"
        else:
            manifest_match = str(metadata.get("comparison_manifest_hash") or "") == manifest.manifest_hash
            capability_id = str(metadata.get("capability_version_id") or "")
            if not capability_id:
                capability_id = next((event.capability_version_id for event in events if event.capability_version_id), "")
            expected_capability = manifest.baseline_capability_version_id if phase == "before" else manifest.candidate_capability_version_id
            if expected_capability and capability_id != expected_capability and not manifest_match:
                return "capability_version_mismatch"
            if phase == "before" and not manifest_match and not expected_capability:
                return "unverified_before_binding"
            if phase == "after" and not manifest_match and not expected_capability and not release_id:
                return "unverified_after_binding"
        tier = self._evidence_tier(trace, metadata)
        if tier == "UNKNOWN" and not metadata.get("verified_bridge") and not manifest.legacy_binding:
            return "unknown_source_evidence_tier"
        if tier != "SIMULATED_TRACE" and not metadata.get("verified_bridge") and not manifest.legacy_binding:
            if str(metadata.get("comparison_manifest_hash") or "") != manifest.manifest_hash:
                return "unverified_cross_source_bridge"
        return ""

    @staticmethod
    def _evidence_tier(trace: Trace, metadata: dict[str, Any]) -> str:
        explicit = str(metadata.get("source_evidence_tier") or metadata.get("evidence_tier") or "")
        if explicit in _EVIDENCE_TIERS:
            return explicit
        if trace.source == "SIMULATED_TRACE":
            return "SIMULATED_TRACE"
        if metadata.get("real_agent") is True:
            return "REAL_AGENT_OBSERVED"
        if metadata.get("isolated_artifact_executed") is True:
            return "ISOLATED_ARTIFACT_EXECUTED"
        if metadata.get("fixture_executed") is True:
            return "FIXTURE_EXECUTED"
        return "UNKNOWN"

    def _conclude(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        window: ObservationWindow,
        manifest: FrozenComparisonManifest,
    ) -> tuple[str, str, list[str]]:
        gaps: list[str] = []
        all_tiers = set(before["source_evidence_tiers"]) | set(after["source_evidence_tiers"])
        if all_tiers and "SIMULATED_TRACE" in all_tiers:
            return "SIMULATION_ONLY", "DEMONSTRATION_ONLY", ["simulated_source"]
        if not all_tiers or "UNKNOWN" in all_tiers:
            gaps.append("unknown_source_evidence_tier")
        minimum = max(0, int(max(window.min_samples, manifest.min_samples)))
        if before["sample_count"] < minimum:
            gaps.append("before_sample_count")
        if after["sample_count"] < minimum:
            gaps.append("after_sample_count")
        if not before["sample_count"] or not after["sample_count"]:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", gaps
        if before["task_categories"] != after["task_categories"] or before["environments"] != after["environments"]:
            gaps.append("task_or_environment_composition_changed")
            return "NOT_COMPARABLE", "INCONCLUSIVE", gaps
        if before["runners"] != after["runners"]:
            gaps.append("runner_changed")
            return "NOT_COMPARABLE", "INCONCLUSIVE", gaps
        if gaps:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", gaps
        configured_unknown_ratio = window.unknown_max_ratio if window.unknown_max_ratio is not None else manifest.unknown_max_ratio
        unknown_limit = min(1.0, max(0.0, float(configured_unknown_ratio)))
        if (before["unknown_rate"] or 0.0) > unknown_limit or (after["unknown_rate"] or 0.0) > unknown_limit:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", ["unknown_ratio_exceeded"]
        if before["n_observed"] < minimum or after["n_observed"] < minimum:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", ["observed_sample_count"]
        if before["failure_rate"] is None or after["failure_rate"] is None:
            return "INSUFFICIENT_EVIDENCE", "INCONCLUSIVE", ["no_observed_outcomes"]
        paired = bool(before["pair_ids"] and before["pair_ids"] == after["pair_ids"])
        if not paired and before["n_total"] != after["n_total"]:
            return "NO_CLEAR_CHANGE", "NO_CLEAR_CHANGE", ["unpaired_sample_size_changed"]
        before_rate = float(before["failure_rate"])
        after_rate = float(after["failure_rate"])
        if after_rate > before_rate:
            return "REGRESSION_SIGNAL", "REGRESSION_SIGNAL", []
        if after_rate < before_rate:
            if all_tiers == {"REAL_AGENT_OBSERVED"}:
                return "IMPROVEMENT_SIGNAL", "SUPPORTED_IMPROVEMENT_SIGNAL", []
            return "IMPROVEMENT_SIGNAL", "EVALUATION_ONLY", ["non_real_agent_source"]
        return "NO_CLEAR_CHANGE", "NO_CLEAR_CHANGE", []

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


__all__ = [
    "FrozenComparisonManifest",
    "ObservationResult",
    "ObservationWindow",
    "ReleaseObservationService",
]
