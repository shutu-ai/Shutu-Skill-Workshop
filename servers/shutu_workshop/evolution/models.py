"""Small, serializable M4 diagnosis, proposal, and evaluation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _items(values: tuple[Any, ...] | list[Any]) -> list[Any]:
    return list(values)


@dataclass(frozen=True)
class EvidenceBundle:
    issue_id: str
    issue_row_version: int
    bundle_hash: str
    issue: dict[str, Any]
    occurrences: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    observed_facts: tuple[dict[str, Any], ...] = ()
    capability_versions: tuple[dict[str, Any], ...] = ()
    unknown_fields: tuple[str, ...] = ()
    user_constraints: tuple[str, ...] = ()
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "issue_row_version": self.issue_row_version,
            "bundle_hash": self.bundle_hash,
            "issue": dict(self.issue),
            "occurrences": _items(self.occurrences),
            "evidence_refs": _items(self.evidence_refs),
            "observed_facts": _items(self.observed_facts),
            "capability_versions": _items(self.capability_versions),
            "unknown_fields": _items(self.unknown_fields),
            "user_constraints": _items(self.user_constraints),
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class DiagnosisHypothesis:
    hypothesis_id: str
    owner_kind: str
    statement: str
    confidence_grade: str
    evidence_refs: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "owner_kind": self.owner_kind,
            "statement": self.statement,
            "confidence_grade": self.confidence_grade,
            "evidence_refs": _items(self.evidence_refs),
        }


@dataclass(frozen=True)
class RecommendedAction:
    target_type: str
    target_capability_id: str
    risk: str
    reason: str
    expected_effect: str
    verification_plan: str
    patch: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_capability_id": self.target_capability_id,
            "risk": self.risk,
            "reason": self.reason,
            "expected_effect": self.expected_effect,
            "verification_plan": self.verification_plan,
            "patch": dict(self.patch),
        }


@dataclass(frozen=True)
class DiagnosisReport:
    diagnosis_id: str
    issue_id: str
    issue_row_version: int
    evidence_bundle_hash: str
    provider_id: str
    model_id: str
    prompt_schema_version: str
    config_hash: str
    observed_facts: tuple[dict[str, Any], ...]
    hypotheses: tuple[DiagnosisHypothesis, ...]
    supporting_evidence: tuple[dict[str, Any], ...]
    contradicting_evidence: tuple[dict[str, Any], ...]
    missing_evidence: tuple[str, ...]
    confidence_grade: str
    recommended_actions: tuple[RecommendedAction, ...]
    status: str
    usage: dict[str, Any]
    created_at: str
    row_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "issue_id": self.issue_id,
            "issue_row_version": self.issue_row_version,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "prompt_schema_version": self.prompt_schema_version,
            "config_hash": self.config_hash,
            "observed_facts": _items(self.observed_facts),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "supporting_evidence": _items(self.supporting_evidence),
            "contradicting_evidence": _items(self.contradicting_evidence),
            "missing_evidence": _items(self.missing_evidence),
            "confidence_grade": self.confidence_grade,
            "recommended_actions": [item.to_dict() for item in self.recommended_actions],
            "status": self.status,
            "usage": dict(self.usage),
            "created_at": self.created_at,
            "row_version": self.row_version,
        }


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    issue_id: str
    diagnosis_id: str
    evidence_bundle_hash: str
    capability_id: str
    capability_version_id: str
    target_hash: str
    category: str
    proposal_version: int
    status: str
    payload: dict[str, Any]
    candidate_hash: str
    reviewer: str
    reviewed_at: str
    evaluation_id: str
    stale_reason: str
    created_at: str
    updated_at: str
    row_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "issue_id": self.issue_id,
            "diagnosis_id": self.diagnosis_id,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "capability_id": self.capability_id,
            "capability_version_id": self.capability_version_id,
            "target_hash": self.target_hash,
            "category": self.category,
            "proposal_version": self.proposal_version,
            "status": self.status,
            "payload": dict(self.payload),
            "candidate_hash": self.candidate_hash,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "evaluation_id": self.evaluation_id,
            "stale_reason": self.stale_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "row_version": self.row_version,
        }


@dataclass(frozen=True)
class TaskCase:
    case_id: str
    input_ref: dict[str, Any]
    expected_behavior: dict[str, Any]
    baseline_output: dict[str, Any]
    holdout: bool = False
    allowed_tools: tuple[str, ...] = ()
    repeatable: bool = True
    production_resources: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "input_ref": dict(self.input_ref),
            "expected_behavior": dict(self.expected_behavior),
            "baseline_output": dict(self.baseline_output),
            "holdout": self.holdout,
            "allowed_tools": _items(self.allowed_tools),
            "repeatable": self.repeatable,
            "production_resources": self.production_resources,
        }


@dataclass(frozen=True)
class EvaluationSuite:
    suite_id: str
    name: str
    suite_version: str
    cases: tuple[TaskCase, ...]
    environment_fingerprint: str = "local-fixture"

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "name": self.name,
            "suite_version": self.suite_version,
            "cases": [item.to_dict() for item in self.cases],
            "environment_fingerprint": self.environment_fingerprint,
        }


@dataclass(frozen=True)
class EvaluationCandidate:
    candidate_id: str
    proposal_id: str
    candidate_hash: str
    capability_version_id: str
    payload: dict[str, Any]
    static_validation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "proposal_id": self.proposal_id,
            "candidate_hash": self.candidate_hash,
            "capability_version_id": self.capability_version_id,
            "payload": dict(self.payload),
            "static_validation": dict(self.static_validation),
        }


@dataclass(frozen=True)
class EvaluationCaseResult:
    result_id: str
    run_id: str
    case_id: str
    side: str
    oracle_verdict: str
    task_success: bool | None
    duration_ms: int | None
    tool_call_count: int | None
    retry_count: int | None
    token_count: int | None
    cost: float | None
    output_ref: dict[str, Any]
    errors: tuple[str, ...]
    observation_completeness: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "side": self.side,
            "oracle_verdict": self.oracle_verdict,
            "task_success": self.task_success,
            "duration_ms": self.duration_ms,
            "tool_call_count": self.tool_call_count,
            "retry_count": self.retry_count,
            "token_count": self.token_count,
            "cost": self.cost,
            "output_ref": dict(self.output_ref),
            "errors": _items(self.errors),
            "observation_completeness": self.observation_completeness,
        }


@dataclass(frozen=True)
class EvaluationVerdict:
    verdict_id: str
    run_id: str
    verdict: str
    statistics: dict[str, Any]
    regression: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "run_id": self.run_id,
            "verdict": self.verdict,
            "statistics": dict(self.statistics),
            "regression": dict(self.regression),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class EvaluationReport:
    run: dict[str, Any]
    candidate: EvaluationCandidate
    results: tuple[EvaluationCaseResult, ...]
    verdict: EvaluationVerdict | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run": dict(self.run),
            "candidate": self.candidate.to_dict(),
            "results": [item.to_dict() for item in self.results],
            "verdict": self.verdict.to_dict() if self.verdict else None,
        }


__all__ = [
    "DiagnosisHypothesis",
    "DiagnosisReport",
    "EvaluationCandidate",
    "EvaluationCaseResult",
    "EvaluationReport",
    "EvaluationSuite",
    "EvaluationVerdict",
    "EvidenceBundle",
    "Proposal",
    "RecommendedAction",
    "TaskCase",
]
