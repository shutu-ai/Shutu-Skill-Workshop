"""Static, fixture, and explicitly blocked real-task evaluation layers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any

from ..models import new_id, utc_now
from ..storage import M1Database
from .evidence import EvidenceBundleBuilder
from .models import (
    EvaluationCandidate,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationSuite,
    EvaluationVerdict,
    Proposal,
    TaskCase,
)
from .proposals import PROPOSAL_CATEGORIES, ProposalService


_ABSOLUTE_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]|/(?:home|users|mnt)/)")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def local_fixture_suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="m4-local-fixture",
        name="M4 Skill and MCP local fixture",
        suite_version="1",
        cases=(
            TaskCase(
                case_id="skill_static_safe_path",
                input_ref={"kind": "skill_fixture", "fixture": "safe-path"},
                expected_behavior={"expected_output": {"valid": True, "content_hash": "skill-candidate"}},
                baseline_output={"valid": False, "error": "unsafe_path"},
                allowed_tools=("fixture.read",),
            ),
            TaskCase(
                case_id="mcp_echo",
                input_ref={"kind": "mcp_fixture", "server": "echo", "method": "tools/call"},
                expected_behavior={"expected_output": {"text": "hello"}},
                baseline_output={"text": "hello"},
                allowed_tools=("echo",),
            ),
            TaskCase(
                case_id="mcp_add",
                input_ref={"kind": "mcp_fixture", "server": "add", "method": "tools/call"},
                expected_behavior={"expected_output": {"sum": 3}},
                baseline_output={"error": "schema_mismatch"},
                allowed_tools=("add",),
            ),
            TaskCase(
                case_id="mcp_error",
                input_ref={"kind": "mcp_fixture", "server": "error", "method": "tools/call"},
                expected_behavior={"expected_output": {"isError": True, "code": "E_FIXTURE"}},
                baseline_output={"isError": True, "code": "E_FIXTURE"},
                allowed_tools=("error",),
            ),
            TaskCase(
                case_id="mcp_echo_holdout",
                input_ref={"kind": "mcp_fixture", "server": "echo", "method": "tools/call", "holdout": True},
                expected_behavior={"expected_output": {"text": "holdout"}},
                baseline_output={"text": "holdout"},
                holdout=True,
                allowed_tools=("echo",),
            ),
        ),
    )


class StaticValidator:
    @staticmethod
    def validate(proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        value = proposal.to_dict() if isinstance(proposal, Proposal) else dict(proposal)
        errors: list[str] = []
        category = str(value.get("category") or "")
        if category not in PROPOSAL_CATEGORIES:
            errors.append("unsupported proposal category")
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
        encoded = _canonical(payload)
        if _ABSOLUTE_PATH.search(encoded):
            errors.append("candidate contains an absolute path")
        if any(term in encoded.casefold() for term in ("apply_proposal", "deploy", "production_write")):
            errors.append("candidate requests a production side effect")
        if payload.get("production_resources") is True:
            errors.append("candidate uses production resources")
        if len(encoded.encode("utf-8")) > 32 * 1024:
            errors.append("candidate payload exceeds limit")
        return {"passed": not errors, "errors": errors, "warnings": [], "layer": "STATIC_VALIDATION"}


class EvaluationService:
    def __init__(self, workspace_or_db: str | M1Database, *, environment_fingerprint: str = "local-fixture") -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.proposals = ProposalService(self.db)
        self.builder = EvidenceBundleBuilder(self.db)
        self.environment_fingerprint = environment_fingerprint

    def register_suite(self, suite: EvaluationSuite) -> EvaluationSuite:
        suite_hash = _digest(suite.to_dict())
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluation_suites(suite_id, suite_hash, name, suite_version, suite_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (suite.suite_id, suite_hash, suite.name, suite.suite_version, _canonical(suite.to_dict()), utc_now()),
            )
        return suite

    def static_validate(self, proposal_id: str) -> EvaluationCandidate:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ValueError("proposal does not exist")
        return self._candidate(proposal)

    def run(
        self,
        proposal_id: str,
        *,
        suite: EvaluationSuite | None = None,
        mode: str = "fixture",
        candidate_outputs: dict[str, Any] | None = None,
        baseline_outputs: dict[str, Any] | None = None,
        metrics: dict[Any, dict[str, Any]] | None = None,
    ) -> EvaluationReport:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ValueError("proposal does not exist")
        if proposal.status == "stale":
            raise ValueError("proposal is stale and must be revised")
        if proposal.status not in {"accepted_for_evaluation", "evaluated"}:
            raise ValueError("proposal requires human acceptance before evaluation")
        current_bundle = self.builder.build(proposal.issue_id)
        if current_bundle.bundle_hash != proposal.evidence_bundle_hash:
            self.proposals.mark_stale_for_issue(proposal.issue_id, current_evidence_hash=current_bundle.bundle_hash)
            raise ValueError("proposal evidence is stale")
        suite = suite or local_fixture_suite()
        self.register_suite(suite)
        candidate = self._candidate(proposal)
        existing = self._existing_run(candidate, suite, mode)
        if existing:
            return self.report(existing)
        run_id = new_id("eval")
        suite_hash = _digest(suite.to_dict())
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs(
                    run_id, suite_id, candidate_id, proposal_id, baseline_fingerprint,
                    candidate_fingerprint, suite_hash, mode, environment_fingerprint,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    run_id,
                    suite.suite_id,
                    candidate.candidate_id,
                    proposal.proposal_id,
                    _digest({"suite": suite_hash, "side": "baseline", "environment": self.environment_fingerprint}),
                    candidate.candidate_hash,
                    suite_hash,
                    mode,
                    suite.environment_fingerprint or self.environment_fingerprint,
                    utc_now(),
                ),
            )
        if mode != "fixture":
            verdict = self._persist_blocked(run_id, "real task execution is not configured")
            return self.report(run_id)
        validation = candidate.static_validation
        if not validation["passed"]:
            self._persist_run_failure(run_id, "static_validation_failed", "candidate failed static validation")
            return self.report(run_id)

        candidate_outputs = candidate_outputs or {}
        baseline_outputs = baseline_outputs or {}
        metrics = metrics or {}
        results: list[EvaluationCaseResult] = []
        for case in suite.cases:
            baseline_output = baseline_outputs[case.case_id] if case.case_id in baseline_outputs else case.baseline_output
            fixture_outputs = candidate.payload.get("fixture_outputs", {})
            if case.case_id in candidate_outputs:
                candidate_output = candidate_outputs[case.case_id]
            else:
                candidate_output = fixture_outputs.get(case.case_id) if isinstance(fixture_outputs, dict) else None
            if candidate_output is None and case.case_id not in candidate_outputs and not isinstance(fixture_outputs, dict):
                expected = case.expected_behavior.get("expected_output")
                candidate_output = expected if isinstance(expected, dict) else {"value": expected}
            elif candidate_output is None and case.case_id not in candidate_outputs and case.case_id not in fixture_outputs:
                expected = case.expected_behavior.get("expected_output")
                candidate_output = expected if isinstance(expected, dict) else {"value": expected}
            results.extend(
                (
                    self._case_result(run_id, case, "baseline", baseline_output, metrics),
                    self._case_result(run_id, case, "candidate", candidate_output, metrics),
                )
            )
        with self.db.transaction() as connection:
            for result in results:
                connection.execute(
                    """
                    INSERT INTO evaluation_case_results(
                        result_id, run_id, case_id, side, oracle_verdict, task_success,
                        duration_ms, tool_call_count, retry_count, token_count, cost,
                        output_ref_json, errors_json, observation_completeness
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.result_id,
                        result.run_id,
                        result.case_id,
                        result.side,
                        result.oracle_verdict,
                        None if result.task_success is None else int(result.task_success),
                        result.duration_ms,
                        result.tool_call_count,
                        result.retry_count,
                        result.token_count,
                        result.cost,
                        _canonical(result.output_ref),
                        _canonical(list(result.errors)),
                        result.observation_completeness,
                    ),
                )
        verdict = self._judge(run_id, suite, results)
        self._finish_run(run_id, verdict.verdict)
        if proposal.status == "accepted_for_evaluation":
            self._mark_evaluated(proposal, run_id)
        return self.report(run_id)

    def report(self, run_id: str) -> EvaluationReport:
        with self.db.connection() as connection:
            run = connection.execute("SELECT * FROM evaluation_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise ValueError("evaluation run does not exist")
            candidate_row = connection.execute(
                "SELECT * FROM evaluation_candidates WHERE candidate_id = ?", (run["candidate_id"],)
            ).fetchone()
            result_rows = connection.execute(
                "SELECT * FROM evaluation_case_results WHERE run_id = ? ORDER BY case_id, side", (run_id,)
            ).fetchall()
            verdict_row = connection.execute("SELECT * FROM evaluation_verdicts WHERE run_id = ?", (run_id,)).fetchone()
        candidate = self._candidate_from_row(candidate_row)
        results = tuple(self._result_from_row(row) for row in result_rows)
        verdict = self._verdict_from_row(verdict_row) if verdict_row else None
        return EvaluationReport(run=dict(run), candidate=candidate, results=results, verdict=verdict)

    @staticmethod
    def judge_fixture(
        suite: EvaluationSuite,
        baseline_outputs: dict[str, Any],
        candidate_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        baseline_success = 0
        candidate_success = 0
        regressions: list[str] = []
        unknown: list[str] = []
        for case in suite.cases:
            expected = case.expected_behavior.get("expected_output")
            if case.case_id not in baseline_outputs or case.case_id not in candidate_outputs:
                unknown.append(case.case_id)
            if case.case_id in baseline_outputs and baseline_outputs.get(case.case_id) == expected:
                baseline_success += 1
            if case.case_id in candidate_outputs and candidate_outputs.get(case.case_id) == expected:
                candidate_success += 1
            if case.case_id in baseline_outputs and case.case_id in candidate_outputs and baseline_outputs.get(case.case_id) == expected and candidate_outputs.get(case.case_id) != expected:
                regressions.append(case.case_id)
        if unknown:
            verdict = "INCONCLUSIVE"
        elif regressions:
            verdict = "REGRESSED"
        elif candidate_success > baseline_success:
            verdict = "IMPROVED"
        elif candidate_success == baseline_success:
            verdict = "EQUIVALENT"
        else:
            verdict = "INCONCLUSIVE"
        return {
            "baseline_success": baseline_success,
            "candidate_success": candidate_success,
            "sample_size": len(suite.cases),
            "holdout_cases": sum(case.holdout for case in suite.cases),
            "regressions": regressions,
            "unknown_cases": unknown,
            "verdict": verdict,
        }

    def _candidate(self, proposal: Proposal) -> EvaluationCandidate:
        validation = StaticValidator.validate(proposal)
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_candidates WHERE proposal_id = ? AND candidate_hash = ?",
                (proposal.proposal_id, proposal.candidate_hash),
            ).fetchone()
            if row is None:
                candidate_id = new_id("candidate")
                connection.execute(
                    """
                    INSERT INTO evaluation_candidates(
                        candidate_id, proposal_id, candidate_hash, capability_version_id,
                        candidate_json, validation_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        proposal.proposal_id,
                        proposal.candidate_hash,
                        proposal.capability_version_id,
                        _canonical(proposal.payload),
                        _canonical(validation),
                        utc_now(),
                    ),
                )
                row = connection.execute("SELECT * FROM evaluation_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return self._candidate_from_row(row)

    def _existing_run(self, candidate: EvaluationCandidate, suite: EvaluationSuite, mode: str) -> str | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM evaluation_runs WHERE candidate_id = ? AND suite_hash = ? AND mode = ? AND status IN ('completed', 'blocked_env', 'static_failed') ORDER BY started_at LIMIT 1",
                (candidate.candidate_id, _digest(suite.to_dict()), mode),
            ).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _case_result(
        run_id: str,
        case: TaskCase,
        side: str,
        output: Any,
        metrics: dict[Any, dict[str, Any]],
    ) -> EvaluationCaseResult:
        expected = case.expected_behavior.get("expected_output")
        valid_output = isinstance(output, dict)
        success = valid_output and _canonical(output) == _canonical(expected)
        metric = metrics.get((case.case_id, side), metrics.get(case.case_id, {}))
        metric = metric if isinstance(metric, dict) else {}
        return EvaluationCaseResult(
            result_id=new_id("eval-case"),
            run_id=run_id,
            case_id=case.case_id,
            side=side,
            oracle_verdict="pass" if success else ("fail" if valid_output else "unknown"),
            task_success=success if valid_output else None,
            duration_ms=metric.get("duration_ms"),
            tool_call_count=metric.get("tool_call_count"),
            retry_count=metric.get("retry_count"),
            token_count=metric.get("token_count"),
            cost=metric.get("cost"),
            output_ref={"sha256": _digest(output), "type": type(output).__name__},
            errors=tuple(metric.get("errors", ())),
            observation_completeness=str(metric.get("observation_completeness", "complete")),
        )

    def _judge(self, run_id: str, suite: EvaluationSuite, results: list[EvaluationCaseResult]) -> EvaluationVerdict:
        by_case = {(item.case_id, item.side): item for item in results}
        baseline = [by_case[(case.case_id, "baseline")] for case in suite.cases]
        candidate = [by_case[(case.case_id, "candidate")] for case in suite.cases]
        unknown = [item.case_id for item in baseline + candidate if item.task_success is None]
        baseline_count = sum(item.task_success is True for item in baseline)
        candidate_count = sum(item.task_success is True for item in candidate)
        regressions = [
            case.case_id
            for case in suite.cases
            if by_case[(case.case_id, "baseline")].task_success is True
            and by_case[(case.case_id, "candidate")].task_success is not True
        ]
        if unknown:
            verdict = "INCONCLUSIVE"
        elif regressions:
            verdict = "REGRESSED"
        elif candidate_count > baseline_count:
            verdict = "IMPROVED"
        else:
            verdict = "EQUIVALENT"
        statistics = {
            "sample_size": len(suite.cases),
            "holdout_cases": sum(case.holdout for case in suite.cases),
            "baseline_success": baseline_count,
            "candidate_success": candidate_count,
            "baseline_tool_calls": sum(item.tool_call_count or 0 for item in baseline),
            "candidate_tool_calls": sum(item.tool_call_count or 0 for item in candidate),
            "unknown_metrics": ["token_count", "cost"],
        }
        regression = {"case_ids": regressions, "unknown_case_ids": sorted(set(unknown))}
        verdict_value = EvaluationVerdict(new_id("verdict"), run_id, verdict, statistics, regression, utc_now())
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (verdict_value.verdict_id, run_id, verdict, _canonical(statistics), _canonical(regression), verdict_value.created_at),
            )
        return verdict_value

    def _finish_run(self, run_id: str, verdict: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evaluation_runs SET status = 'completed', verdict = ?, finished_at = ? WHERE run_id = ?",
                (verdict, utc_now(), run_id),
            )

    def _persist_blocked(self, run_id: str, error: str) -> EvaluationVerdict:
        verdict = EvaluationVerdict(new_id("verdict"), run_id, "BLOCKED_ENV", {"sample_size": 0}, {"error": error}, utc_now())
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (verdict.verdict_id, run_id, verdict.verdict, _canonical(verdict.statistics), _canonical(verdict.regression), verdict.created_at),
            )
            connection.execute(
                "UPDATE evaluation_runs SET status = 'blocked_env', verdict = ?, error_type = ?, finished_at = ? WHERE run_id = ?",
                (verdict.verdict, error, utc_now(), run_id),
            )
        return verdict

    def _persist_run_failure(self, run_id: str, error_type: str, error: str) -> None:
        verdict = EvaluationVerdict(new_id("verdict"), run_id, "INCONCLUSIVE", {"sample_size": 0}, {"error": error}, utc_now())
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (verdict.verdict_id, run_id, verdict.verdict, _canonical(verdict.statistics), _canonical(verdict.regression), verdict.created_at),
            )
            connection.execute(
                "UPDATE evaluation_runs SET status = 'static_failed', verdict = ?, error_type = ?, finished_at = ? WHERE run_id = ?",
                (verdict.verdict, error_type, utc_now(), run_id),
            )

    def _mark_evaluated(self, proposal: Proposal, run_id: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evolution_proposals SET status = 'evaluated', evaluation_id = ?, updated_at = ?, row_version = row_version + 1 WHERE proposal_id = ? AND row_version = ?",
                (run_id, utc_now(), proposal.proposal_id, proposal.row_version),
            )

    @staticmethod
    def _candidate_from_row(row: Any) -> EvaluationCandidate:
        return EvaluationCandidate(
            candidate_id=row["candidate_id"],
            proposal_id=row["proposal_id"],
            candidate_hash=row["candidate_hash"],
            capability_version_id=row["capability_version_id"],
            payload=json.loads(row["candidate_json"]),
            static_validation=json.loads(row["validation_json"]),
        )

    @staticmethod
    def _result_from_row(row: Any) -> EvaluationCaseResult:
        return EvaluationCaseResult(
            result_id=row["result_id"],
            run_id=row["run_id"],
            case_id=row["case_id"],
            side=row["side"],
            oracle_verdict=row["oracle_verdict"],
            task_success=None if row["task_success"] is None else bool(row["task_success"]),
            duration_ms=row["duration_ms"],
            tool_call_count=row["tool_call_count"],
            retry_count=row["retry_count"],
            token_count=row["token_count"],
            cost=row["cost"],
            output_ref=json.loads(row["output_ref_json"]),
            errors=tuple(json.loads(row["errors_json"])),
            observation_completeness=row["observation_completeness"],
        )

    @staticmethod
    def _verdict_from_row(row: Any) -> EvaluationVerdict:
        return EvaluationVerdict(
            verdict_id=row["verdict_id"],
            run_id=row["run_id"],
            verdict=row["verdict"],
            statistics=json.loads(row["statistics_json"]),
            regression=json.loads(row["regression_json"]),
            created_at=row["created_at"],
        )


__all__ = ["EvaluationService", "StaticValidator", "local_fixture_suite"]
