"""Auditable, non-automating feedback from observation to M3/M4 objects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..models import utc_now
from ..storage import M1Database
from ..traces import TraceStore
from .observation import ObservationResult


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OptimizationFeedback:
    feedback_id: str
    release_id: str
    observation_id: str
    experiment_id: str
    baseline_capability_version_id: str
    candidate_capability_version_id: str
    baseline_artifact_hash: str
    candidate_artifact_hash: str
    verdict: str
    signal: str
    source_tier: str
    evidence_quality: str
    issue_id: str
    issue_row_version: int | None
    diagnosis_id: str
    proposal_id: str
    evaluation_id: str
    included_trace_digests: tuple[dict[str, str], ...]
    excluded_trace_digests: tuple[dict[str, str], ...]
    comparison_manifest_hash: str
    metrics: dict[str, Any]
    feedback_state: str
    recommended_next_action: str
    reviewed_at: str
    created_at: str
    revision: int
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["included_trace_digests"] = list(self.included_trace_digests)
        value["excluded_trace_digests"] = list(self.excluded_trace_digests)
        return value


class OptimizationFeedbackService:
    """Record links only when referenced rows and traces actually exist."""

    def __init__(self, workspace_or_db: str | Path | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.traces = TraceStore(self.db)

    def record(
        self,
        observation: ObservationResult | dict[str, Any],
        *,
        issue_id: str = "",
        diagnosis_id: str = "",
        proposal_id: str = "",
        evaluation_id: str = "",
        experiment_id: str = "",
        reviewed_at: str = "",
    ) -> OptimizationFeedback:
        result = observation if isinstance(observation, ObservationResult) else ObservationResult(**dict(observation))
        summary = result.summary if isinstance(result.summary, dict) else {}
        manifest = summary.get("comparison_manifest") if isinstance(summary.get("comparison_manifest"), dict) else {}
        before = summary.get("before") if isinstance(summary.get("before"), dict) else {}
        after = summary.get("after") if isinstance(summary.get("after"), dict) else {}
        release_id = result.release_id
        issue_id, issue_row_version = self._valid_issue_link(issue_id, before, after)
        diagnosis_id = self._valid_diagnosis_link(diagnosis_id, issue_id)
        proposal_id = self._valid_proposal_link(proposal_id, issue_id, diagnosis_id)
        evaluation_id = self._valid_evaluation_link(evaluation_id)
        included = self._trace_digests(before, after)
        excluded = self._excluded_digests(before, after)
        tiers = set(before.get("source_evidence_tiers", [])) | set(after.get("source_evidence_tiers", []))
        source_tier = self._source_tier(tiers)
        signal = str(summary.get("signal") or result.conclusion)
        state, next_action, quality = self._state(result.conclusion, signal, source_tier)
        metrics = {
            "before": before,
            "after": after,
            "data_gaps": list(summary.get("data_gaps", [])),
            "false_exact_join_count": int(summary.get("false_exact_join_count", 0) or 0),
            "source_fingerprint": summary.get("source_fingerprint", ""),
        }
        fingerprint = _digest(
            {
                "release_id": release_id,
                "observation_id": result.observation_id,
                "comparison_manifest_hash": summary.get("comparison_manifest_hash", ""),
                "signal": signal,
                "source_tier": source_tier,
                "metrics": metrics,
                "links": [issue_id, diagnosis_id, proposal_id, evaluation_id],
            }
        )
        with self.db.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM optimization_feedback WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
        if existing is not None:
            return self._from_row(existing)
        with self.db.connection() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(revision), 0) + 1
                FROM optimization_feedback
                WHERE observation_id = ? AND comparison_manifest_hash = ?
                """,
                (result.observation_id, str(summary.get("comparison_manifest_hash") or "")),
            ).fetchone()
        revision = int(row[0]) if row else 1
        now = utc_now()
        feedback = OptimizationFeedback(
            feedback_id=f"feedback_{fingerprint[:24]}",
            release_id=release_id,
            observation_id=result.observation_id,
            experiment_id=experiment_id or str(manifest.get("experiment_id") or ""),
            baseline_capability_version_id=str(manifest.get("baseline_capability_version_id") or ""),
            candidate_capability_version_id=str(manifest.get("candidate_capability_version_id") or ""),
            baseline_artifact_hash=str(summary.get("baseline_artifact_hash") or manifest.get("baseline_artifact_hash") or ""),
            candidate_artifact_hash=str(summary.get("candidate_artifact_hash") or manifest.get("candidate_artifact_hash") or ""),
            verdict=result.conclusion,
            signal=signal,
            source_tier=source_tier,
            evidence_quality=quality,
            issue_id=issue_id,
            issue_row_version=issue_row_version,
            diagnosis_id=diagnosis_id,
            proposal_id=proposal_id,
            evaluation_id=evaluation_id,
            included_trace_digests=tuple(included),
            excluded_trace_digests=tuple(excluded),
            comparison_manifest_hash=str(summary.get("comparison_manifest_hash") or ""),
            metrics=metrics,
            feedback_state=state,
            recommended_next_action=next_action,
            reviewed_at=reviewed_at,
            created_at=now,
            revision=revision,
            fingerprint=fingerprint,
        )
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO optimization_feedback(
                    feedback_id, release_id, observation_id, experiment_id,
                    baseline_capability_version_id, candidate_capability_version_id,
                    baseline_artifact_hash, candidate_artifact_hash, verdict, signal,
                    source_tier, evidence_quality, issue_id, issue_row_version,
                    diagnosis_id, proposal_id, evaluation_id,
                    included_trace_digests_json, excluded_trace_digests_json,
                    comparison_manifest_hash, metrics_json, feedback_state,
                    recommended_next_action, reviewed_at, created_at, revision,
                    fingerprint, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback.feedback_id,
                    feedback.release_id,
                    feedback.observation_id,
                    feedback.experiment_id,
                    feedback.baseline_capability_version_id,
                    feedback.candidate_capability_version_id,
                    feedback.baseline_artifact_hash,
                    feedback.candidate_artifact_hash,
                    feedback.verdict,
                    feedback.signal,
                    feedback.source_tier,
                    feedback.evidence_quality,
                    feedback.issue_id,
                    feedback.issue_row_version,
                    feedback.diagnosis_id,
                    feedback.proposal_id,
                    feedback.evaluation_id,
                    _canonical(list(feedback.included_trace_digests)),
                    _canonical(list(feedback.excluded_trace_digests)),
                    feedback.comparison_manifest_hash,
                    _canonical(feedback.metrics),
                    feedback.feedback_state,
                    feedback.recommended_next_action,
                    feedback.reviewed_at,
                    feedback.created_at,
                    feedback.revision,
                    feedback.fingerprint,
                    _canonical(feedback.to_dict()),
                ),
            )
        return feedback

    def get(self, feedback_id: str) -> OptimizationFeedback | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM optimization_feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
        return self._from_row(row) if row else None

    def list(self, *, release_id: str = "", observation_id: str = "", limit: int = 100) -> list[OptimizationFeedback]:
        if limit < 1:
            raise ValueError("limit must be positive")
        conditions: list[str] = []
        values: list[Any] = []
        if release_id:
            conditions.append("release_id = ?")
            values.append(release_id)
        if observation_id:
            conditions.append("observation_id = ?")
            values.append(observation_id)
        query = "SELECT * FROM optimization_feedback"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at, revision LIMIT ?"
        values.append(limit)
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._from_row(row) for row in rows]

    def _valid_issue_link(self, issue_id: str, before: dict[str, Any], after: dict[str, Any]) -> tuple[str, int | None]:
        if not issue_id:
            return "", None
        trace_ids = list(before.get("trace_ids", [])) + list(after.get("trace_ids", []))
        if not trace_ids:
            return "", None
        placeholders = ",".join("?" for _ in trace_ids)
        with self.db.connection() as connection:
            issue = connection.execute("SELECT row_version FROM issue_clusters WHERE issue_id = ?", (issue_id,)).fetchone()
            occurrence = connection.execute(
                f"SELECT 1 FROM issue_occurrences WHERE issue_id = ? AND trace_id IN ({placeholders}) LIMIT 1",
                [issue_id, *trace_ids],
            ).fetchone()
        if issue is None or occurrence is None:
            return "", None
        return issue_id, int(issue["row_version"])

    def _valid_diagnosis_link(self, diagnosis_id: str, issue_id: str) -> str:
        if not diagnosis_id or not issue_id:
            return ""
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM diagnosis_reports WHERE diagnosis_id = ? AND issue_id = ?",
                (diagnosis_id, issue_id),
            ).fetchone()
        return diagnosis_id if row else ""

    def _valid_proposal_link(self, proposal_id: str, issue_id: str, diagnosis_id: str) -> str:
        if not proposal_id or not issue_id:
            return ""
        with self.db.connection() as connection:
            row = connection.execute("SELECT issue_id, diagnosis_id FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None or row["issue_id"] != issue_id or (diagnosis_id and row["diagnosis_id"] != diagnosis_id):
            return ""
        return proposal_id

    def _valid_evaluation_link(self, evaluation_id: str) -> str:
        if not evaluation_id:
            return ""
        with self.db.connection() as connection:
            row = connection.execute("SELECT 1 FROM evaluation_runs WHERE run_id = ?", (evaluation_id,)).fetchone()
        return evaluation_id if row else ""

    @staticmethod
    def _trace_digests(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for phase, value in (("before", before), ("after", after)):
            digests = value.get("trace_digests", {})
            if isinstance(digests, dict):
                result.extend({"phase": phase, "trace_id": str(key), "digest": str(item)} for key, item in sorted(digests.items()))
        return result

    @staticmethod
    def _excluded_digests(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"phase": phase, "reason": str(reason), "count": str(count)}
            for phase, value in (("before", before), ("after", after))
            for reason, count in sorted((value.get("excluded") or {}).items())
        ]

    @staticmethod
    def _source_tier(tiers: set[str]) -> str:
        if not tiers:
            return "UNKNOWN"
        if "SIMULATED_TRACE" in tiers:
            return "SIMULATED_TRACE"
        if len(tiers) == 1:
            return next(iter(tiers))
        return "+".join(sorted(tiers))

    @staticmethod
    def _state(conclusion: str, signal: str, source_tier: str) -> tuple[str, str, str]:
        if source_tier == "SIMULATED_TRACE":
            return "DEMONSTRATION_ONLY", "NO_PRODUCTION_ACTION", "simulated"
        if conclusion in {"INSUFFICIENT_EVIDENCE", "NOT_COMPARABLE"}:
            return "NEEDS_MORE_EVIDENCE", "OBSERVATION_GAP", "insufficient"
        if conclusion == "REGRESSION_SIGNAL":
            return "REVIEW_REQUIRED", "HUMAN_REVIEW_OR_CONTROLLED_ROLLBACK", "signal_only"
        if signal == "SUPPORTED_IMPROVEMENT_SIGNAL" and source_tier == "REAL_AGENT_OBSERVED":
            return "SUPPORTED_IMPROVEMENT_SIGNAL", "HUMAN_REVIEW", "real_observed"
        if conclusion == "IMPROVEMENT_SIGNAL":
            return "EVALUATION_ONLY", "NO_PRODUCTION_ACTION", "non_real_agent"
        return "NO_ACTION", "NO_PRODUCTION_ACTION", "insufficient_change"

    @staticmethod
    def _from_row(row: Any) -> OptimizationFeedback:
        return OptimizationFeedback(
            feedback_id=row["feedback_id"],
            release_id=row["release_id"],
            observation_id=row["observation_id"],
            experiment_id=row["experiment_id"],
            baseline_capability_version_id=row["baseline_capability_version_id"],
            candidate_capability_version_id=row["candidate_capability_version_id"],
            baseline_artifact_hash=row["baseline_artifact_hash"],
            candidate_artifact_hash=row["candidate_artifact_hash"],
            verdict=row["verdict"],
            signal=row["signal"],
            source_tier=row["source_tier"],
            evidence_quality=row["evidence_quality"],
            issue_id=row["issue_id"],
            issue_row_version=int(row["issue_row_version"]) if row["issue_row_version"] is not None else None,
            diagnosis_id=row["diagnosis_id"],
            proposal_id=row["proposal_id"],
            evaluation_id=row["evaluation_id"],
            included_trace_digests=tuple(json.loads(row["included_trace_digests_json"])),
            excluded_trace_digests=tuple(json.loads(row["excluded_trace_digests_json"])),
            comparison_manifest_hash=row["comparison_manifest_hash"],
            metrics=json.loads(row["metrics_json"]),
            feedback_state=row["feedback_state"],
            recommended_next_action=row["recommended_next_action"],
            reviewed_at=row["reviewed_at"],
            created_at=row["created_at"],
            revision=int(row["revision"]),
            fingerprint=row["fingerprint"],
        )


__all__ = ["OptimizationFeedback", "OptimizationFeedbackService"]
