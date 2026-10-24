"""SQLite-backed issue ledger and bounded incremental analysis."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Iterable

from ..models import TraceEvent, new_id, utc_now
from ..storage import M1Database
from ..traces import TraceStore
from .detector import RuleDetector, RuleRegistry
from .models import (
    AnalyzeResult,
    AttributionHypothesis,
    BatchResult,
    ConcurrentUpdateError,
    DetectorContext,
    EvidenceRef,
    Issue,
    IssueDetails,
    Occurrence,
    RuleFinding,
    RuleSpec,
)


ISSUE_STATUSES = frozenset({"open", "acknowledged", "resolved", "ignored"})
_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
_CONFIDENCE_RANK = {"unknown": 0, "weak": 1, "moderate_evidence": 2, "high_evidence": 3}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any, length: int = 64) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _json(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class DiagnosisService:
    """Runs deterministic rules off the ingestion path and stores only evidence refs."""

    def __init__(
        self,
        workspace_or_db: str | M1Database,
        *,
        detector: RuleDetector | None = None,
        environment_fingerprint: str = "",
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.traces = TraceStore(self.db)
        self.detector = detector or RuleDetector()
        self.environment_fingerprint = environment_fingerprint

    def rules(self) -> list[RuleSpec]:
        return self.detector.registry.list()

    def analyze_trace(
        self,
        trace_id: str,
        ruleset: RuleRegistry | Iterable[RuleSpec] | None = None,
    ) -> AnalyzeResult:
        detector = self._detector(ruleset)
        trace = self.traces.get(trace_id)
        if trace is None:
            raise ValueError("trace does not exist")
        ruleset_fingerprint = detector.ruleset_fingerprint()
        revision = self._trace_revision(trace, self.traces.list_events(trace_id))
        checkpoint = self._checkpoint(trace_id, ruleset_fingerprint)
        if checkpoint and checkpoint["status"] == "completed" and checkpoint["trace_revision_fingerprint"] == revision:
            return AnalyzeResult(
                trace_id=trace_id,
                run_id=checkpoint["run_id"],
                status="skipped",
                skipped=True,
                ruleset_fingerprint=ruleset_fingerprint,
                trace_revision_fingerprint=revision,
            )
        run_id = self._start_run(detector, ruleset_fingerprint)
        try:
            result = self._analyze_trace_with_run(trace_id, detector, ruleset_fingerprint, run_id)
            self._finish_run(run_id, status="completed")
            return result
        except BaseException as exc:
            self._finish_run(run_id, status="failed", errors=(type(exc).__name__,))
            raise

    def analyze_new(
        self,
        limit: int = 100,
        since: str | None = None,
        ruleset: RuleRegistry | Iterable[RuleSpec] | None = None,
    ) -> BatchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        detector = self._detector(ruleset)
        ruleset_fingerprint = detector.ruleset_fingerprint()
        trace_ids = self._trace_ids(limit, since)
        run_id = self._start_run(detector, ruleset_fingerprint)
        analyzed = skipped = findings = occurrences = duplicates = 0
        errors: list[str] = []
        for trace_id in trace_ids:
            try:
                result = self._analyze_trace_with_run(trace_id, detector, ruleset_fingerprint, run_id)
            except BaseException as exc:
                errors.append(f"{trace_id}:{type(exc).__name__}")
                continue
            if result.skipped:
                skipped += 1
            else:
                analyzed += 1
                findings += len(result.findings)
            occurrences += result.occurrence_count
            duplicates += result.duplicate_occurrences
        status = "failed" if errors and not analyzed and not skipped else ("partial" if errors else "completed")
        self._finish_run(run_id, status=status, errors=tuple(errors))
        return BatchResult(
            run_id=run_id,
            status=status,
            trace_count=len(trace_ids),
            analyzed_count=analyzed,
            skipped_count=skipped,
            findings_count=findings,
            occurrence_count=occurrences,
            duplicate_occurrences=duplicates,
            errors=tuple(errors),
        )

    def list_issues(
        self,
        filters: dict[str, Any] | None = None,
        *,
        status: str | None = None,
        workspace_id: str | None = None,
        rule_id: str | None = None,
        limit: int = 100,
    ) -> list[Issue]:
        filters = filters or {}
        status = status or filters.get("status")
        workspace_id = workspace_id or filters.get("workspace_id")
        rule_id = rule_id or filters.get("rule_id")
        if "limit" in filters and limit == 100:
            limit = int(filters["limit"])
        if limit < 1:
            raise ValueError("limit must be positive")
        query = "SELECT * FROM issue_clusters"
        conditions: list[str] = []
        values: list[Any] = []
        if status:
            conditions.append("status = ?")
            values.append(status)
        if workspace_id:
            conditions.append("workspace_id = ?")
            values.append(workspace_id)
        if rule_id:
            conditions.append("rule_id = ?")
            values.append(rule_id)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY last_seen_at DESC, issue_id LIMIT ?"
        values.append(limit)
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._issue_from_row(row) for row in rows]

    def show_issue(self, issue_id: str) -> IssueDetails | None:
        with self.db.connection() as connection:
            issue_row = connection.execute(
                "SELECT * FROM issue_clusters WHERE issue_id = ?", (issue_id,)
            ).fetchone()
            if issue_row is None:
                return None
            rows = connection.execute(
                "SELECT * FROM issue_occurrences WHERE issue_id = ? ORDER BY observed_at, occurrence_id",
                (issue_id,),
            ).fetchall()
        return IssueDetails(
            issue=self._issue_from_row(issue_row),
            occurrences=tuple(self._occurrence_from_row(row) for row in rows),
        )

    def set_issue_status(
        self,
        issue_id: str,
        status: str,
        expected_row_version: int | None = None,
        *,
        reason: str = "cli",
    ) -> Issue:
        if status not in ISSUE_STATUSES:
            raise ValueError(f"unsupported issue status: {status}")
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM issue_clusters WHERE issue_id = ?", (issue_id,)
            ).fetchone()
            if row is None:
                raise ValueError("issue does not exist")
            current_version = int(row["row_version"])
            if expected_row_version is not None and expected_row_version != current_version:
                raise ConcurrentUpdateError("issue row_version changed")
            old_status = str(row["status"])
            if old_status == status:
                return self._issue_from_row(row)
            new_version = current_version + 1
            now = utc_now()
            updated = connection.execute(
                """
                UPDATE issue_clusters
                SET status = ?, updated_at = ?, row_version = ?
                WHERE issue_id = ? AND row_version = ?
                """,
                (status, now, new_version, issue_id, current_version),
            )
            if updated.rowcount != 1:
                raise ConcurrentUpdateError("issue row_version changed")
            connection.execute(
                """
                INSERT INTO issue_status_history(
                    history_id, issue_id, old_status, new_status,
                    old_row_version, new_row_version, changed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id("issue-status"), issue_id, old_status, status, current_version, new_version, now, reason),
            )
            changed = connection.execute(
                "SELECT * FROM issue_clusters WHERE issue_id = ?", (issue_id,)
            ).fetchone()
        return self._issue_from_row(changed)

    def _detector(self, ruleset: RuleRegistry | Iterable[RuleSpec] | None) -> RuleDetector:
        if ruleset is None:
            return self.detector
        if isinstance(ruleset, RuleRegistry):
            return RuleDetector(ruleset)
        return RuleDetector(RuleRegistry(ruleset))

    def _trace_ids(self, limit: int, since: str | None) -> list[str]:
        query = "SELECT trace_id FROM traces"
        values: list[Any] = []
        if since:
            query += " WHERE started_at >= ?"
            values.append(since)
        query += " ORDER BY started_at, rowid LIMIT ?"
        values.append(limit)
        with self.db.connection() as connection:
            return [str(row[0]) for row in connection.execute(query, values)]

    def _checkpoint(self, trace_id: str, ruleset_fingerprint: str) -> Any:
        with self.db.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM analysis_checkpoints
                WHERE trace_id = ? AND ruleset_fingerprint = ?
                """,
                (trace_id, ruleset_fingerprint),
            ).fetchone()

    def _start_run(self, detector: RuleDetector, ruleset_fingerprint: str) -> str:
        run_id = new_id("analysis")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs(
                    run_id, detector_version, ruleset_fingerprint,
                    started_at, status, environment_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, detector.detector_version, ruleset_fingerprint, utc_now(), "running", self.environment_fingerprint),
            )
        return run_id

    def _finish_run(self, run_id: str, *, status: str, errors: tuple[str, ...] = ()) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE analysis_runs SET status = ?, finished_at = ?, errors_summary = ? WHERE run_id = ?",
                (status, utc_now(), "; ".join(errors)[:2000], run_id),
            )

    def _analyze_trace_with_run(
        self,
        trace_id: str,
        detector: RuleDetector,
        ruleset_fingerprint: str,
        run_id: str,
    ) -> AnalyzeResult:
        trace = self.traces.get(trace_id)
        if trace is None:
            raise ValueError("trace does not exist")
        events = self.traces.list_events(trace_id)
        revision = self._trace_revision(trace, events)
        checkpoint = self._checkpoint(trace_id, ruleset_fingerprint)
        if checkpoint and checkpoint["status"] == "completed" and checkpoint["trace_revision_fingerprint"] == revision:
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE analysis_runs SET skipped_count = skipped_count + 1 WHERE run_id = ?", (run_id,)
                )
            return AnalyzeResult(
                trace_id=trace_id,
                run_id=run_id,
                status="skipped",
                skipped=True,
                ruleset_fingerprint=ruleset_fingerprint,
                trace_revision_fingerprint=revision,
            )
        findings = detector.detect(DetectorContext(trace=trace, ordered_events=tuple(events)))
        insufficient = sum(1 for finding in findings if finding.attribution_state == "insufficient_evidence")
        usable = tuple(finding for finding in findings if finding.attribution_state != "insufficient_evidence")
        inserted, duplicates, issue_ids = self._replace_occurrences(
            trace,
            usable,
            revision,
            ruleset_fingerprint,
            run_id,
            findings_count=len(findings),
            skipped_count=insufficient,
        )
        return AnalyzeResult(
            trace_id=trace_id,
            run_id=run_id,
            status="completed",
            skipped=False,
            ruleset_fingerprint=ruleset_fingerprint,
            trace_revision_fingerprint=revision,
            findings=findings,
            occurrence_count=inserted,
            duplicate_occurrences=duplicates,
            insufficient_evidence=insufficient,
            issue_ids=tuple(sorted(issue_ids)),
        )

    def _replace_occurrences(
        self,
        trace: Any,
        findings: tuple[RuleFinding, ...],
        revision: str,
        ruleset_fingerprint: str,
        run_id: str,
        *,
        findings_count: int,
        skipped_count: int,
    ) -> tuple[int, int, set[str]]:
        inserted = duplicates = 0
        issue_ids: set[str] = set()
        now = utc_now()
        with self.db.transaction() as connection:
            old_issue_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT issue_id FROM issue_occurrences WHERE trace_id = ?", (trace.trace_id,)
                )
            ]
            connection.execute("DELETE FROM issue_occurrences WHERE trace_id = ?", (trace.trace_id,))
            for issue_id in old_issue_ids:
                self._refresh_cluster(connection, issue_id)

            for finding in findings:
                dedup_key = _digest(
                    {
                        "workspace_id": trace.workspace_id,
                        "rule_id": finding.rule_id,
                        "rule_version": finding.rule_version,
                        "category": finding.category,
                        "signature": finding.signature,
                        "capability_id": finding.capability_id,
                    }
                )
                row = connection.execute(
                    "SELECT * FROM issue_clusters WHERE dedup_key = ?", (dedup_key,)
                ).fetchone()
                if row is None:
                    issue_id = new_id("issue")
                    owner = finding.owner_candidates[0].to_dict() if finding.owner_candidates else {"owner_kind": "unknown"}
                    connection.execute(
                        """
                        INSERT INTO issue_clusters(
                            issue_id, dedup_key, rule_id, rule_version, category,
                            normalized_signature, workspace_id, primary_capability_id,
                            first_seen_at, last_seen_at, severity, status,
                            occurrence_count, confidence_grade, primary_owner_hypothesis,
                            attribution_state, created_at, updated_at, row_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            issue_id,
                            dedup_key,
                            finding.rule_id,
                            finding.rule_version,
                            finding.category,
                            finding.signature,
                            trace.workspace_id,
                            finding.capability_id,
                            finding.detected_at,
                            finding.detected_at,
                            finding.severity,
                            finding.confidence_or_evidence_grade,
                            _canonical(owner),
                            finding.attribution_state,
                            now,
                            now,
                        ),
                    )
                else:
                    issue_id = str(row["issue_id"])
                issue_ids.add(issue_id)
                fingerprint = _digest(
                    {
                        "trace_id": trace.trace_id,
                        "rule_id": finding.rule_id,
                        "rule_version": finding.rule_version,
                        "signature": finding.signature,
                        "events": [ref.event_id for ref in finding.evidence_refs],
                        "capability_version_id": finding.capability_version_id,
                    }
                )
                occurrence_id = f"occurrence_{fingerprint[:32]}"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO issue_occurrences(
                        occurrence_id, issue_id, finding_fingerprint, trace_id,
                        observed_at, rule_id, rule_version, capability_id,
                        capability_version_id, evidence_refs_json, source_scope_json,
                        error_fingerprint, owner_candidates_json, unknown_fields_json,
                        evidence_quality, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        occurrence_id,
                        issue_id,
                        fingerprint,
                        trace.trace_id,
                        finding.detected_at,
                        finding.rule_id,
                        finding.rule_version,
                        finding.capability_id,
                        finding.capability_version_id,
                        _canonical([item.to_dict() for item in finding.evidence_refs]),
                        _canonical(finding.source_scope),
                        finding.error_fingerprint,
                        _canonical([item.to_dict() for item in finding.owner_candidates]),
                        _canonical(list(finding.unknown_fields)),
                        finding.confidence_or_evidence_grade,
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    duplicates += 1
                    continue
                inserted += 1
                self._increment_cluster(connection, issue_id, finding, trace.workspace_id)

            for issue_id in old_issue_ids:
                self._refresh_cluster(connection, issue_id)
            connection.execute(
                """
                INSERT INTO analysis_checkpoints(
                    trace_id, ruleset_fingerprint, trace_revision_fingerprint,
                    analyzed_at, run_id, status
                ) VALUES (?, ?, ?, ?, ?, 'completed')
                ON CONFLICT(trace_id, ruleset_fingerprint) DO UPDATE SET
                    trace_revision_fingerprint = excluded.trace_revision_fingerprint,
                    analyzed_at = excluded.analyzed_at,
                    run_id = excluded.run_id,
                    status = excluded.status
                """,
                (trace.trace_id, ruleset_fingerprint, revision, now, run_id),
            )
            connection.execute(
                """
                UPDATE analysis_runs
                SET trace_count = trace_count + 1,
                    findings_count = findings_count + ?,
                    skipped_count = skipped_count + ?
                WHERE run_id = ?
                """,
                (findings_count, skipped_count, run_id),
            )
        return inserted, duplicates, issue_ids

    @staticmethod
    def _refresh_cluster(connection: Any, issue_id: str) -> None:
        row = connection.execute(
            "SELECT COUNT(*) AS count, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen FROM issue_occurrences WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        count = int(row["count"])
        if count == 0:
            connection.execute("DELETE FROM issue_clusters WHERE issue_id = ?", (issue_id,))
            return
        connection.execute(
            "UPDATE issue_clusters SET occurrence_count = ?, first_seen_at = ?, last_seen_at = ?, updated_at = ? WHERE issue_id = ?",
            (count, row["first_seen"], row["last_seen"], utc_now(), issue_id),
        )

    @staticmethod
    def _increment_cluster(connection: Any, issue_id: str, finding: RuleFinding, workspace_id: str) -> None:
        row = connection.execute("SELECT * FROM issue_clusters WHERE issue_id = ?", (issue_id,)).fetchone()
        if row is None:
            return
        current_status = str(row["status"])
        new_status = "open" if current_status in {"resolved", "ignored"} else current_status
        new_severity = finding.severity if _SEVERITY_RANK.get(finding.severity, 0) > _SEVERITY_RANK.get(row["severity"], 0) else row["severity"]
        new_confidence = finding.confidence_or_evidence_grade if _CONFIDENCE_RANK.get(finding.confidence_or_evidence_grade, 0) > _CONFIDENCE_RANK.get(row["confidence_grade"], 0) else row["confidence_grade"]
        version_bump = 1 if new_status != current_status else 0
        connection.execute(
            """
            UPDATE issue_clusters
            SET occurrence_count = occurrence_count + 1,
                last_seen_at = CASE WHEN last_seen_at < ? THEN ? ELSE last_seen_at END,
                severity = ?, confidence_grade = ?, status = ?,
                updated_at = ?, row_version = row_version + ?
            WHERE issue_id = ?
            """,
            (finding.detected_at, finding.detected_at, new_severity, new_confidence, new_status, utc_now(), version_bump, issue_id),
        )
        if new_status != current_status:
            connection.execute(
                """
                INSERT INTO issue_status_history(
                    history_id, issue_id, old_status, new_status,
                    old_row_version, new_row_version, changed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id("issue-status"), issue_id, current_status, new_status, row["row_version"], row["row_version"] + 1, utc_now(), "rule_reopened"),
            )

    @staticmethod
    def _trace_revision(trace: Any, events: list[TraceEvent]) -> str:
        identities = []
        for event in events:
            identities.append(
                {
                    "event_id": event.event_id,
                    "seq": event.seq,
                    "digest": _digest(event.to_dict()),
                }
            )
        return _digest(
            {
                "trace_id": trace.trace_id,
                "status": trace.status,
                "task_result": trace.task_result,
                "observation_completeness": trace.observation_completeness,
                "event_count": len(events),
                "events": sorted(identities, key=lambda item: (item["event_id"], item["seq"])),
            }
        )

    @staticmethod
    def _issue_from_row(row: Any) -> Issue:
        return Issue(
            issue_id=row["issue_id"],
            dedup_key=row["dedup_key"],
            rule_id=row["rule_id"],
            rule_version=row["rule_version"],
            category=row["category"],
            normalized_signature=row["normalized_signature"],
            workspace_id=row["workspace_id"],
            primary_capability_id=row["primary_capability_id"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            severity=row["severity"],
            status=row["status"],
            occurrence_count=int(row["occurrence_count"]),
            confidence_grade=row["confidence_grade"],
            primary_owner_hypothesis=_json(row["primary_owner_hypothesis"], {"owner_kind": "unknown"}),
            attribution_state=row["attribution_state"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            row_version=int(row["row_version"]),
        )

    @staticmethod
    def _occurrence_from_row(row: Any) -> Occurrence:
        evidence = tuple(
            EvidenceRef(
                trace_id=item.get("trace_id", ""),
                event_id=item.get("event_id", ""),
                capability_version_id=item.get("capability_version_id", ""),
                correlation_scope=item.get("correlation_scope", {}) if isinstance(item.get("correlation_scope", {}), dict) else {},
            )
            for item in _json(row["evidence_refs_json"], [])
            if isinstance(item, dict)
        )
        owners = tuple(
            AttributionHypothesis(
                owner_kind=item.get("owner_kind", "unknown"),
                owner_capability_id=item.get("owner_capability_id", ""),
                state=item.get("state", "hypothesis"),
                basis=item.get("basis", "explicit_error"),
                confidence_grade=item.get("confidence_grade", "unknown"),
                contributing_capabilities=tuple(item.get("contributing_capabilities", [])),
                required_verification=tuple(item.get("required_verification", [])),
            )
            for item in _json(row["owner_candidates_json"], [])
            if isinstance(item, dict)
        )
        return Occurrence(
            occurrence_id=row["occurrence_id"],
            issue_id=row["issue_id"],
            finding_fingerprint=row["finding_fingerprint"],
            trace_id=row["trace_id"],
            observed_at=row["observed_at"],
            rule_id=row["rule_id"],
            rule_version=row["rule_version"],
            capability_id=row["capability_id"],
            capability_version_id=row["capability_version_id"],
            evidence_refs=evidence,
            source_scope=_json(row["source_scope_json"], {}),
            error_fingerprint=row["error_fingerprint"],
            owner_candidates=owners,
            unknown_fields=tuple(_json(row["unknown_fields_json"], [])),
            evidence_quality=row["evidence_quality"],
            created_at=row["created_at"],
        )


IssueCluster = Issue
IssueOccurrence = Occurrence


__all__ = [
    "DiagnosisService",
    "ISSUE_STATUSES",
    "IssueCluster",
    "IssueOccurrence",
]
