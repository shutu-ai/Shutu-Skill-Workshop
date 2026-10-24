"""Static, fixture, and explicitly blocked real-task evaluation layers."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import new_id, utc_now
from ..storage import M1Database
from .evidence import EvidenceBundleBuilder
from .models import (
    EvaluationCandidate,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationSuite,
    EvaluationVerdict,
    ExecutableCandidate,
    ExecutableCaseInput,
    FixtureExecution,
    Proposal,
    TaskCase,
)
from .proposals import PROPOSAL_CATEGORIES, ProposalService


_ABSOLUTE_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]|/(?:home|users|mnt)/)")
_EVALUATION_LOCK = threading.RLock()
_FIXTURE_ORACLE_ID = "expected-output-oracle"
_FIXTURE_ORACLE_VERSION = "2"
_FIXTURE_EVIDENCE = "FIXTURE_EXECUTED"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


_FIXTURE_ARTIFACTS: dict[str, dict[str, str]] = {
    "m4-fixture-baseline-v2": {
        "skill_static_safe_path": "reject",
        "mcp_echo": "echo",
        "mcp_add": "legacy_schema",
        "mcp_error": "error",
        "mcp_echo_holdout": "echo",
    },
    "m4-fixture-candidate-v2": {
        "skill_static_safe_path": "accept",
        "mcp_echo": "echo",
        "mcp_add": "add",
        "mcp_error": "error",
        "mcp_echo_holdout": "echo",
    },
    "m4-fixture-bad-v2": {
        "skill_static_safe_path": "accept",
        "mcp_echo": "bad_echo",
        "mcp_add": "add",
        "mcp_error": "error",
        "mcp_echo_holdout": "echo",
    },
}


def _artifact_hash(artifact_ref: str) -> str:
    definition = _FIXTURE_ARTIFACTS.get(artifact_ref)
    return _digest({"artifact_ref": artifact_ref, "definition": definition}) if definition is not None else ""


def local_fixture_candidate_payload(artifact_ref: str = "m4-fixture-candidate-v2") -> dict[str, Any]:
    """Return a reference to a checked-in, deterministic fixture artifact."""

    if artifact_ref not in _FIXTURE_ARTIFACTS:
        raise ValueError(f"unknown local fixture artifact: {artifact_ref}")
    return {"fixture_artifact_id": artifact_ref, "fixture_artifact_hash": _artifact_hash(artifact_ref)}


def local_fixture_suite() -> EvaluationSuite:
    """A real local execution suite; expected answers remain Oracle-only data."""

    return EvaluationSuite(
        suite_id="m4-local-fixture-v2",
        name="M4 Skill and MCP local fixture (executed)",
        suite_version="2",
        cases=(
            TaskCase(
                case_id="skill_static_safe_path",
                input_ref={
                    "kind": "skill_fixture",
                    "fixture": "safe-path",
                    "task_input": {"path": "docs/guide.md", "content": "skill-candidate"},
                },
                expected_behavior={"expected_output": {"valid": True, "content_hash": "skill-candidate"}},
                baseline_output={"valid": False, "error": "unsafe_path"},
                allowed_tools=("fixture.read",),
            ),
            TaskCase(
                case_id="mcp_echo",
                input_ref={
                    "kind": "mcp_fixture",
                    "server": "echo",
                    "method": "tools/call",
                    "task_input": {"text": "hello"},
                },
                expected_behavior={"expected_output": {"text": "hello"}},
                baseline_output={"text": "hello"},
                allowed_tools=("echo",),
            ),
            TaskCase(
                case_id="mcp_add",
                input_ref={
                    "kind": "mcp_fixture",
                    "server": "add",
                    "method": "tools/call",
                    "task_input": {"a": 1, "b": 2},
                },
                expected_behavior={"expected_output": {"sum": 3}},
                baseline_output={"error": "schema_mismatch"},
                allowed_tools=("add",),
            ),
            TaskCase(
                case_id="mcp_error",
                input_ref={
                    "kind": "mcp_fixture",
                    "server": "error",
                    "method": "tools/call",
                    "task_input": {"code": "E_FIXTURE"},
                },
                expected_behavior={"expected_output": {"isError": True, "code": "E_FIXTURE"}},
                baseline_output={"isError": True, "code": "E_FIXTURE"},
                allowed_tools=("error",),
            ),
            TaskCase(
                case_id="mcp_echo_holdout",
                input_ref={
                    "kind": "mcp_fixture",
                    "server": "echo",
                    "method": "tools/call",
                    "task_input": {"text": "holdout"},
                    "report_as": "holdout",
                },
                expected_behavior={"expected_output": {"text": "holdout"}},
                baseline_output={"text": "holdout"},
                holdout=True,
                allowed_tools=("echo",),
            ),
        ),
    )


class FixtureRunner(Protocol):
    runner_id: str
    runner_version: str
    runner_config_hash: str

    def supports(self, case_input: ExecutableCaseInput) -> bool:
        ...

    def execute(self, case_input: ExecutableCaseInput, candidate: ExecutableCandidate) -> FixtureExecution:
        ...


class LocalFixtureRunner:
    """Bounded, side-effect-free runner for the evaluation mechanism itself."""

    runner_id = "shutu.local-fixture-runner"
    runner_version = "2"

    def __init__(self) -> None:
        self.runner_config_hash = _digest(
            {
                "runner_id": self.runner_id,
                "runner_version": self.runner_version,
                "artifact_refs": sorted(_FIXTURE_ARTIFACTS),
            }
        )

    @staticmethod
    def artifact_hash(artifact_ref: str) -> str:
        return _artifact_hash(artifact_ref)

    def supports(self, case_input: ExecutableCaseInput) -> bool:
        return case_input.case_id in next(iter(_FIXTURE_ARTIFACTS.values()), {})

    def execute(self, case_input: ExecutableCaseInput, candidate: ExecutableCandidate) -> FixtureExecution:
        started = time.perf_counter()
        task_input_hash = _digest(case_input.task_input)
        definition = _FIXTURE_ARTIFACTS.get(candidate.immutable_artifact_ref)
        common = {
            "runner_id": self.runner_id,
            "runner_version": self.runner_version,
            "runner_config_hash": self.runner_config_hash,
            "artifact_hash": candidate.artifact_hash,
            "task_input_hash": task_input_hash,
            "environment_fingerprint": "local-fixture",
        }
        if definition is None or candidate.artifact_hash != _artifact_hash(candidate.immutable_artifact_ref):
            return FixtureExecution(
                execution_status="failed",
                provenance="fixture_executed",
                errors=("artifact_missing_or_hash_mismatch",),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                **common,
            )
        behavior = definition.get(case_input.case_id)
        if behavior is None or not isinstance(case_input.task_input, dict):
            return FixtureExecution(
                execution_status="failed",
                provenance="fixture_executed",
                errors=("unsupported_fixture_case",),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                **common,
            )
        value = case_input.task_input
        try:
            if behavior == "reject":
                output: Any = {"valid": False, "error": "unsafe_path"}
            elif behavior == "accept":
                output = {"valid": True, "content_hash": value.get("content", "")}
            elif behavior == "echo":
                output = {"text": value.get("text", "")}
            elif behavior == "bad_echo":
                output = {"text": "wrong"}
            elif behavior == "legacy_schema":
                output = {"error": "schema_mismatch"}
            elif behavior == "add" and isinstance(value.get("a"), (int, float)) and isinstance(value.get("b"), (int, float)):
                output = {"sum": value["a"] + value["b"]}
            elif behavior == "error":
                output = {"isError": True, "code": str(value.get("code") or "E_FIXTURE")}
            else:
                return FixtureExecution(
                    execution_status="failed",
                    provenance="fixture_executed",
                    errors=("fixture_execution_failed",),
                    duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                    **common,
                )
        except (TypeError, ValueError, KeyError):
            return FixtureExecution(
                execution_status="failed",
                provenance="fixture_executed",
                errors=("fixture_execution_failed",),
                duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                **common,
            )
        return FixtureExecution(
            output=output,
            execution_status="completed",
            provenance="fixture_executed",
            output_digest=_digest(output),
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            tool_call_count=1,
            **common,
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


@dataclass(frozen=True)
class _ExecutionContext:
    suite_hash: str
    manifest_hash: str
    manifest: dict[str, Any]
    environment_fingerprint: str
    runner_id: str
    runner_version: str
    runner_config_hash: str
    oracle_id: str
    oracle_version: str
    baseline: ExecutableCandidate
    candidate: ExecutableCandidate
    baseline_fingerprint: str
    candidate_fingerprint: str
    cacheable: bool


class EvaluationService:
    def __init__(
        self,
        workspace_or_db: str | M1Database,
        *,
        environment_fingerprint: str = "local-fixture",
        runner: FixtureRunner | None = None,
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.proposals = ProposalService(self.db)
        self.builder = EvidenceBundleBuilder(self.db)
        self.environment_fingerprint = environment_fingerprint
        self.runner = runner or LocalFixtureRunner()

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
        # The lock closes the check/insert race inside one process; SQLite adds the cross-process uniqueness guard.
        with _EVALUATION_LOCK:
            return self._run(
                proposal_id,
                suite=suite,
                mode=mode,
                candidate_outputs=candidate_outputs,
                baseline_outputs=baseline_outputs,
                metrics=metrics,
            )

    def _run(
        self,
        proposal_id: str,
        *,
        suite: EvaluationSuite | None,
        mode: str,
        candidate_outputs: dict[str, Any] | None,
        baseline_outputs: dict[str, Any] | None,
        metrics: dict[Any, dict[str, Any]] | None,
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
        context = self._execution_context(
            candidate,
            suite,
            mode,
            candidate_outputs=candidate_outputs,
            baseline_outputs=baseline_outputs,
            metrics=metrics,
        )
        if context.cacheable:
            existing = self._existing_run(context.manifest_hash)
            if existing:
                return self.report(existing)
        run_id = new_id("eval")
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_runs(
                        run_id, suite_id, candidate_id, proposal_id, baseline_fingerprint,
                        candidate_fingerprint, suite_hash, mode, environment_fingerprint,
                        status, started_at, execution_manifest_hash, execution_manifest_json,
                        runner_id, runner_version, runner_config_hash, oracle_id, oracle_version,
                        baseline_artifact_hash, candidate_artifact_hash, evidence_tier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        suite.suite_id,
                        candidate.candidate_id,
                        proposal.proposal_id,
                        context.baseline_fingerprint,
                        context.candidate_fingerprint,
                        context.suite_hash,
                        mode,
                        context.environment_fingerprint,
                        utc_now(),
                        context.manifest_hash,
                        _canonical(context.manifest),
                        context.runner_id,
                        context.runner_version,
                        context.runner_config_hash,
                        context.oracle_id,
                        context.oracle_version,
                        context.baseline.artifact_hash,
                        context.candidate.artifact_hash,
                        "STATIC_ONLY",
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self._run_for_manifest(context.manifest_hash)
            if existing:
                return self.report(existing)
            raise
        if mode != "fixture":
            self._persist_blocked(run_id, "real task execution is not configured")
            return self.report(run_id)
        validation = candidate.static_validation
        if not validation["passed"]:
            self._persist_run_failure(run_id, "static_validation_failed", "candidate failed static validation")
            return self.report(run_id)

        results: list[EvaluationCaseResult] = []
        candidate_outputs = candidate_outputs if isinstance(candidate_outputs, dict) else None
        baseline_outputs = baseline_outputs if isinstance(baseline_outputs, dict) else None
        metrics = metrics if isinstance(metrics, dict) else {}
        for case in suite.cases:
            case_input = self._case_input(case)
            baseline_execution = (
                self._imported_execution(case_input, baseline_outputs[case.case_id])
                if baseline_outputs is not None and case.case_id in baseline_outputs
                else self._execute(case_input, context.baseline, context.environment_fingerprint)
            )
            candidate_execution = (
                self._imported_execution(case_input, candidate_outputs[case.case_id])
                if candidate_outputs is not None and case.case_id in candidate_outputs
                else self._execute(case_input, context.candidate, context.environment_fingerprint)
            )
            results.extend(
                (
                    self._case_result(run_id, case, "baseline", baseline_execution, metrics),
                    self._case_result(run_id, case, "candidate", candidate_execution, metrics),
                )
            )
        with self.db.transaction() as connection:
            for result in results:
                connection.execute(
                    """
                    INSERT INTO evaluation_case_results(
                        result_id, run_id, case_id, side, oracle_verdict, task_success,
                        duration_ms, tool_call_count, retry_count, token_count, cost,
                        output_ref_json, errors_json, observation_completeness,
                        execution_status, provenance, runner_id, runner_version,
                        artifact_hash, task_input_hash, environment_fingerprint, output_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        result.execution_status,
                        result.provenance,
                        result.runner_id,
                        result.runner_version,
                        result.artifact_hash,
                        result.task_input_hash,
                        result.environment_fingerprint,
                        result.output_digest,
                    ),
                )
        verdict = self._judge(run_id, suite, results)
        self._finish_run(run_id, verdict.verdict, verdict.evidence_tier)
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
        """Retain the old helper as an explicitly untrusted import path."""

        baseline_success = sum(
            case.case_id in baseline_outputs
            and baseline_outputs.get(case.case_id) == case.expected_behavior.get("expected_output")
            for case in suite.cases
        )
        candidate_success = sum(
            case.case_id in candidate_outputs
            and candidate_outputs.get(case.case_id) == case.expected_behavior.get("expected_output")
            for case in suite.cases
        )
        unknown = [
            case.case_id
            for case in suite.cases
            if case.case_id not in baseline_outputs or case.case_id not in candidate_outputs
        ]
        return {
            "baseline_success": baseline_success,
            "candidate_success": candidate_success,
            "sample_size": len(suite.cases),
            "holdout_cases": sum(case.holdout for case in suite.cases),
            "regressions": [],
            "unknown_cases": unknown,
            "verdict": "INCONCLUSIVE",
            "evidence_tier": "SYNTHETIC_OR_IMPORTED",
            "trust": "LEGACY_UNTRUSTED_EVAL",
        }

    def _execution_context(
        self,
        candidate: EvaluationCandidate,
        suite: EvaluationSuite,
        mode: str,
        *,
        candidate_outputs: dict[str, Any] | None,
        baseline_outputs: dict[str, Any] | None,
        metrics: dict[Any, dict[str, Any]] | None,
    ) -> _ExecutionContext:
        suite_hash = _digest(suite.to_dict())
        environment = suite.environment_fingerprint or self.environment_fingerprint
        if mode == "fixture":
            runner_id = self.runner.runner_id
            runner_version = self.runner.runner_version
            runner_config_hash = self.runner.runner_config_hash
            oracle_id = _FIXTURE_ORACLE_ID
            oracle_version = _FIXTURE_ORACLE_VERSION
            baseline = ExecutableCandidate(
                "baseline",
                "m4-fixture-baseline-v2",
                self._runner_artifact_hash("m4-fixture-baseline-v2"),
                candidate.capability_version_id,
            )
            candidate_ref = str(candidate.payload.get("fixture_artifact_id") or "")
            supplied_hash = str(candidate.payload.get("fixture_artifact_hash") or "")
            known_hash = self._runner_artifact_hash(candidate_ref)
            candidate_artifact_hash = supplied_hash or known_hash
            executable_candidate = ExecutableCandidate(
                "candidate", candidate_ref, candidate_artifact_hash, candidate.capability_version_id
            )
        else:
            runner_id = "real-task-runner"
            runner_version = "unconfigured"
            runner_config_hash = ""
            oracle_id = "real-task-oracle"
            oracle_version = "unconfigured"
            baseline = ExecutableCandidate("baseline", "", "", candidate.capability_version_id)
            executable_candidate = ExecutableCandidate("candidate", "", "", candidate.capability_version_id)
        inputs = [
            {
                "case_id": case.case_id,
                "task_input_hash": _digest(self._case_input(case).to_dict()),
                "allowed_tools": list(case.allowed_tools),
                "limits": self._case_input(case).timeout_and_resource_limits,
            }
            for case in suite.cases
        ]
        legacy_inputs = {
            "candidate_outputs": None if candidate_outputs is None else _digest(candidate_outputs),
            "baseline_outputs": None if baseline_outputs is None else _digest(baseline_outputs),
            "metrics": None if metrics is None else _digest(metrics),
        }
        manifest: dict[str, Any] = {
            "baseline_artifact_hash": baseline.artifact_hash,
            "candidate_artifact_hash": executable_candidate.artifact_hash,
            "candidate_hash": candidate.candidate_hash,
            "suite_hash": suite_hash,
            "oracle_id": oracle_id,
            "oracle_version": oracle_version,
            "runner_id": runner_id,
            "runner_version": runner_version,
            "runner_config_hash": runner_config_hash,
            "environment_fingerprint": environment,
            "mode": mode,
            "inputs": inputs,
            "legacy_inputs": legacy_inputs,
        }
        supported = mode == "fixture" and all(self.runner.supports(self._case_input(case)) for case in suite.cases)
        cacheable = mode == "fixture" and bool(executable_candidate.immutable_artifact_ref and executable_candidate.artifact_hash)
        cacheable = cacheable and supported and all(value is None for value in legacy_inputs.values())
        if not cacheable:
            manifest["uncacheable_nonce"] = new_id("manifest")
        manifest_hash = _digest(manifest)
        return _ExecutionContext(
            suite_hash=suite_hash,
            manifest_hash=manifest_hash,
            manifest=manifest,
            environment_fingerprint=environment,
            runner_id=runner_id,
            runner_version=runner_version,
            runner_config_hash=runner_config_hash,
            oracle_id=oracle_id,
            oracle_version=oracle_version,
            baseline=baseline,
            candidate=executable_candidate,
            baseline_fingerprint=_digest({"artifact": baseline.artifact_hash, "inputs": inputs}),
            candidate_fingerprint=_digest({"artifact": executable_candidate.artifact_hash, "candidate": candidate.candidate_hash, "inputs": inputs}),
            cacheable=cacheable,
        )

    def _runner_artifact_hash(self, artifact_ref: str) -> str:
        artifact_hash = getattr(self.runner, "artifact_hash", None)
        return str(artifact_hash(artifact_ref) or "") if callable(artifact_hash) else ""

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

    def _existing_run(self, manifest_hash: str) -> str | None:
        with self.db.connection() as connection:
            row = connection.execute(
                """
                SELECT run_id FROM evaluation_runs
                WHERE execution_manifest_hash = ?
                  AND status = 'completed'
                  AND verdict IN ('IMPROVED', 'EQUIVALENT', 'REGRESSED')
                  AND evidence_tier IN ('FIXTURE_EXECUTED', 'REAL_TASK_EXECUTED')
                ORDER BY started_at LIMIT 1
                """,
                (manifest_hash,),
            ).fetchone()
        return str(row[0]) if row else None

    def _run_for_manifest(self, manifest_hash: str) -> str | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM evaluation_runs WHERE execution_manifest_hash = ? ORDER BY started_at LIMIT 1",
                (manifest_hash,),
            ).fetchone()
        return str(row[0]) if row else None

    def _execute(
        self,
        case_input: ExecutableCaseInput,
        candidate: ExecutableCandidate,
        environment_fingerprint: str,
    ) -> FixtureExecution:
        if not candidate.immutable_artifact_ref or not candidate.artifact_hash:
            return FixtureExecution(
                execution_status="not_executed",
                provenance="imported_unverified",
                runner_id=getattr(self.runner, "runner_id", ""),
                runner_version=getattr(self.runner, "runner_version", ""),
                runner_config_hash=getattr(self.runner, "runner_config_hash", ""),
                artifact_hash=candidate.artifact_hash,
                task_input_hash=_digest(case_input.task_input),
                environment_fingerprint=environment_fingerprint,
                errors=("no_verified_fixture_artifact",),
            )
        result = self.runner.execute(case_input, candidate)
        if result.environment_fingerprint == "":
            return FixtureExecution(
                output=result.output,
                execution_status=result.execution_status,
                provenance=result.provenance,
                runner_id=result.runner_id,
                runner_version=result.runner_version,
                runner_config_hash=result.runner_config_hash,
                artifact_hash=result.artifact_hash,
                task_input_hash=result.task_input_hash,
                environment_fingerprint=environment_fingerprint,
                output_digest=result.output_digest,
                errors=result.errors,
                duration_ms=result.duration_ms,
                tool_call_count=result.tool_call_count,
                retry_count=result.retry_count,
            )
        return result

    @staticmethod
    def _imported_execution(case_input: ExecutableCaseInput, output: Any) -> FixtureExecution:
        return FixtureExecution(
            output=output,
            execution_status="imported",
            provenance="imported_unverified",
            runner_id="legacy-output-import",
            runner_version="unverified",
            task_input_hash=_digest(case_input.task_input),
            output_digest=_digest(output),
            errors=("output_not_produced_by_runner",),
        )

    @staticmethod
    def _case_input(case: TaskCase) -> ExecutableCaseInput:
        input_ref = case.input_ref if isinstance(case.input_ref, dict) else {}
        task_input = input_ref.get("task_input")
        if task_input is None:
            task_input = {
                key: value
                for key, value in input_ref.items()
                if key not in {"holdout", "expected_behavior", "baseline_output"}
            }
        limits = input_ref.get("timeout_and_resource_limits", {})
        return ExecutableCaseInput(
            case_id=case.case_id,
            task_input=task_input,
            allowed_tools=case.allowed_tools,
            timeout_and_resource_limits=limits if isinstance(limits, dict) else {},
        )

    @staticmethod
    def _case_result(
        run_id: str,
        case: TaskCase,
        side: str,
        execution: FixtureExecution,
        metrics: dict[Any, dict[str, Any]],
    ) -> EvaluationCaseResult:
        expected = case.expected_behavior.get("expected_output")
        valid_output = isinstance(execution.output, dict)
        trusted = execution.execution_status == "completed" and execution.provenance in {"fixture_executed", "real_executed"}
        success = trusted and valid_output and _canonical(execution.output) == _canonical(expected)
        metric = metrics.get((case.case_id, side), metrics.get(case.case_id, {}))
        metric = metric if isinstance(metric, dict) else {}
        errors = tuple(execution.errors) + tuple(metric.get("errors", ()))
        output_digest = execution.output_digest or (_digest(execution.output) if execution.output is not None else "")
        return EvaluationCaseResult(
            result_id=new_id("eval-case"),
            run_id=run_id,
            case_id=case.case_id,
            side=side,
            oracle_verdict="pass" if success else ("fail" if trusted and valid_output else "unknown"),
            task_success=success if trusted and valid_output else None,
            duration_ms=execution.duration_ms if execution.duration_ms is not None else metric.get("duration_ms"),
            tool_call_count=execution.tool_call_count if execution.tool_call_count is not None else metric.get("tool_call_count"),
            retry_count=execution.retry_count if execution.retry_count is not None else metric.get("retry_count"),
            token_count=metric.get("token_count"),
            cost=metric.get("cost"),
            output_ref={
                "sha256": output_digest,
                "type": type(execution.output).__name__,
                "provenance": execution.provenance,
                "execution_status": execution.execution_status,
            },
            errors=errors,
            observation_completeness=str(metric.get("observation_completeness", "complete" if trusted else "unknown")),
            execution_status=execution.execution_status,
            provenance=execution.provenance,
            runner_id=execution.runner_id,
            runner_version=execution.runner_version,
            artifact_hash=execution.artifact_hash,
            task_input_hash=execution.task_input_hash,
            environment_fingerprint=execution.environment_fingerprint,
            output_digest=output_digest,
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
        evidence_tier = self._evidence_tier(results)
        statistics = {
            "sample_size": len(suite.cases),
            "holdout_cases": sum(case.holdout for case in suite.cases),
            "baseline_success": baseline_count,
            "candidate_success": candidate_count,
            "baseline_tool_calls": sum(item.tool_call_count or 0 for item in baseline),
            "candidate_tool_calls": sum(item.tool_call_count or 0 for item in candidate),
            "unknown_case_ids": sorted(set(unknown)),
            "evidence_tier": evidence_tier,
            "unknown_metrics": ["token_count", "cost"],
        }
        regression = {"case_ids": regressions, "unknown_case_ids": sorted(set(unknown))}
        verdict_value = EvaluationVerdict(
            new_id("verdict"), run_id, verdict, statistics, regression, utc_now(), evidence_tier
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at, evidence_tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    verdict_value.verdict_id,
                    run_id,
                    verdict,
                    _canonical(statistics),
                    _canonical(regression),
                    verdict_value.created_at,
                    evidence_tier,
                ),
            )
        return verdict_value

    @staticmethod
    def _evidence_tier(results: list[EvaluationCaseResult]) -> str:
        if results and all(item.provenance == "real_executed" for item in results):
            return "REAL_TASK_EXECUTED"
        if results and all(item.provenance == "fixture_executed" for item in results):
            return _FIXTURE_EVIDENCE
        if any(item.provenance == "imported_unverified" for item in results):
            return "SYNTHETIC_OR_IMPORTED"
        return "STATIC_ONLY"

    def _finish_run(self, run_id: str, verdict: str, evidence_tier: str) -> None:
        status = "completed" if verdict in {"IMPROVED", "EQUIVALENT", "REGRESSED"} else "inconclusive"
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE evaluation_runs SET status = ?, verdict = ?, evidence_tier = ?, finished_at = ? WHERE run_id = ?",
                (status, verdict, evidence_tier, utc_now(), run_id),
            )

    def _persist_blocked(self, run_id: str, error: str) -> EvaluationVerdict:
        verdict = EvaluationVerdict(
            new_id("verdict"), run_id, "BLOCKED_ENV", {"sample_size": 0}, {"error": error}, utc_now(), "STATIC_ONLY"
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at, evidence_tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    verdict.verdict_id,
                    run_id,
                    verdict.verdict,
                    _canonical(verdict.statistics),
                    _canonical(verdict.regression),
                    verdict.created_at,
                    verdict.evidence_tier,
                ),
            )
            connection.execute(
                "UPDATE evaluation_runs SET status = 'blocked_env', verdict = ?, evidence_tier = ?, error_type = ?, finished_at = ? WHERE run_id = ?",
                (verdict.verdict, verdict.evidence_tier, error, utc_now(), run_id),
            )
        return verdict

    def _persist_run_failure(self, run_id: str, error_type: str, error: str) -> None:
        verdict = EvaluationVerdict(
            new_id("verdict"), run_id, "INCONCLUSIVE", {"sample_size": 0}, {"error": error}, utc_now(), "STATIC_ONLY"
        )
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO evaluation_verdicts(verdict_id, run_id, verdict, statistics_json, regression_json, created_at, evidence_tier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    verdict.verdict_id,
                    run_id,
                    verdict.verdict,
                    _canonical(verdict.statistics),
                    _canonical(verdict.regression),
                    verdict.created_at,
                    verdict.evidence_tier,
                ),
            )
            connection.execute(
                "UPDATE evaluation_runs SET status = 'static_failed', verdict = ?, evidence_tier = ?, error_type = ?, finished_at = ? WHERE run_id = ?",
                (verdict.verdict, verdict.evidence_tier, error_type, utc_now(), run_id),
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
            execution_status=row["execution_status"],
            provenance=row["provenance"],
            runner_id=row["runner_id"],
            runner_version=row["runner_version"],
            artifact_hash=row["artifact_hash"],
            task_input_hash=row["task_input_hash"],
            environment_fingerprint=row["environment_fingerprint"],
            output_digest=row["output_digest"],
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
            evidence_tier=row["evidence_tier"],
        )


__all__ = [
    "EvaluationService",
    "FixtureRunner",
    "LocalFixtureRunner",
    "StaticValidator",
    "local_fixture_candidate_payload",
    "local_fixture_suite",
]
