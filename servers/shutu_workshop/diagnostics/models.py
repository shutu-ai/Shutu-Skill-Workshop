"""Serializable contracts for deterministic M3 diagnosis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Trace, TraceEvent


def _list(value: tuple[Any, ...] | list[Any]) -> list[Any]:
    return list(value)


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    rule_version: str = "1"
    required_fields: tuple[str, ...] = ()
    scope: str = "trace"
    severity_policy: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "required_fields": list(self.required_fields),
            "scope": self.scope,
            "severity_policy": dict(self.severity_policy),
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class EvidenceRef:
    trace_id: str
    event_id: str
    capability_version_id: str = ""
    correlation_scope: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "capability_version_id": self.capability_version_id,
            "correlation_scope": dict(self.correlation_scope),
        }


@dataclass(frozen=True)
class AttributionHypothesis:
    owner_kind: str = "unknown"
    owner_capability_id: str = ""
    state: str = "hypothesis"
    basis: str = "explicit_error"
    confidence_grade: str = "unknown"
    contributing_capabilities: tuple[str, ...] = ()
    required_verification: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_kind": self.owner_kind,
            "owner_capability_id": self.owner_capability_id,
            "state": self.state,
            "basis": self.basis,
            "confidence_grade": self.confidence_grade,
            "contributing_capabilities": _list(self.contributing_capabilities),
            "required_verification": _list(self.required_verification),
        }


@dataclass(frozen=True)
class DetectorContext:
    trace: Trace
    ordered_events: tuple[TraceEvent, ...] = ()
    snapshots: dict[str, Any] = field(default_factory=dict)
    scope_map: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleFinding:
    finding_id: str
    rule_id: str
    signature: str
    category: str
    severity: str
    observed_fact: dict[str, Any]
    evidence_refs: tuple[EvidenceRef, ...]
    owner_candidates: tuple[AttributionHypothesis, ...]
    attribution_state: str
    confidence_or_evidence_grade: str
    unknown_fields: tuple[str, ...]
    detected_at: str
    detector_version: str
    rule_version: str = "1"
    trace_id: str = ""
    capability_id: str = ""
    capability_version_id: str = ""
    source_scope: dict[str, Any] = field(default_factory=dict)
    error_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "signature": self.signature,
            "category": self.category,
            "severity": self.severity,
            "observed_fact": dict(self.observed_fact),
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "owner_candidates": [item.to_dict() for item in self.owner_candidates],
            "attribution_state": self.attribution_state,
            "confidence_or_evidence_grade": self.confidence_or_evidence_grade,
            "unknown_fields": _list(self.unknown_fields),
            "detected_at": self.detected_at,
            "detector_version": self.detector_version,
            "trace_id": self.trace_id,
            "capability_id": self.capability_id,
            "capability_version_id": self.capability_version_id,
            "source_scope": dict(self.source_scope),
            "error_fingerprint": self.error_fingerprint,
        }


@dataclass(frozen=True)
class Issue:
    issue_id: str
    dedup_key: str
    rule_id: str
    rule_version: str
    category: str
    normalized_signature: str
    workspace_id: str
    primary_capability_id: str
    first_seen_at: str
    last_seen_at: str
    severity: str
    status: str
    occurrence_count: int
    confidence_grade: str
    primary_owner_hypothesis: dict[str, Any]
    attribution_state: str
    created_at: str
    updated_at: str
    row_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "dedup_key": self.dedup_key,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "category": self.category,
            "normalized_signature": self.normalized_signature,
            "workspace_id": self.workspace_id,
            "primary_capability_id": self.primary_capability_id,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "severity": self.severity,
            "status": self.status,
            "occurrence_count": self.occurrence_count,
            "confidence_grade": self.confidence_grade,
            "primary_owner_hypothesis": dict(self.primary_owner_hypothesis),
            "attribution_state": self.attribution_state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "row_version": self.row_version,
        }


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str
    issue_id: str
    finding_fingerprint: str
    trace_id: str
    observed_at: str
    rule_id: str
    rule_version: str
    capability_id: str
    capability_version_id: str
    evidence_refs: tuple[EvidenceRef, ...]
    source_scope: dict[str, Any]
    error_fingerprint: str
    owner_candidates: tuple[AttributionHypothesis, ...]
    unknown_fields: tuple[str, ...]
    evidence_quality: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "issue_id": self.issue_id,
            "finding_fingerprint": self.finding_fingerprint,
            "trace_id": self.trace_id,
            "observed_at": self.observed_at,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "capability_id": self.capability_id,
            "capability_version_id": self.capability_version_id,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "source_scope": dict(self.source_scope),
            "error_fingerprint": self.error_fingerprint,
            "owner_candidates": [item.to_dict() for item in self.owner_candidates],
            "unknown_fields": _list(self.unknown_fields),
            "evidence_quality": self.evidence_quality,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class IssueDetails:
    issue: Issue
    occurrences: tuple[Occurrence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue.to_dict(),
            "occurrences": [item.to_dict() for item in self.occurrences],
        }


@dataclass(frozen=True)
class AnalyzeResult:
    trace_id: str
    run_id: str
    status: str
    skipped: bool
    ruleset_fingerprint: str
    trace_revision_fingerprint: str
    findings: tuple[RuleFinding, ...] = ()
    occurrence_count: int = 0
    duplicate_occurrences: int = 0
    insufficient_evidence: int = 0
    issue_ids: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "status": self.status,
            "skipped": self.skipped,
            "ruleset_fingerprint": self.ruleset_fingerprint,
            "trace_revision_fingerprint": self.trace_revision_fingerprint,
            "findings": [item.to_dict() for item in self.findings],
            "occurrence_count": self.occurrence_count,
            "duplicate_occurrences": self.duplicate_occurrences,
            "insufficient_evidence": self.insufficient_evidence,
            "issue_ids": _list(self.issue_ids),
            "errors": _list(self.errors),
        }


@dataclass(frozen=True)
class BatchResult:
    run_id: str
    status: str
    trace_count: int
    analyzed_count: int
    skipped_count: int
    findings_count: int
    occurrence_count: int
    duplicate_occurrences: int
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "trace_count": self.trace_count,
            "analyzed_count": self.analyzed_count,
            "skipped_count": self.skipped_count,
            "findings_count": self.findings_count,
            "occurrence_count": self.occurrence_count,
            "duplicate_occurrences": self.duplicate_occurrences,
            "errors": _list(self.errors),
        }


class ConcurrentUpdateError(RuntimeError):
    """Raised when an issue row changed before an optimistic update."""
