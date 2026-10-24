"""Deterministic M3 rule detection and issue ledger."""

from .detector import RuleDetector, RuleRegistry, default_rule_specs, rule_specs
from .ledger import DiagnosisService, ISSUE_STATUSES, IssueCluster, IssueOccurrence
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

__all__ = [
    "AnalyzeResult",
    "AttributionHypothesis",
    "BatchResult",
    "ConcurrentUpdateError",
    "DiagnosisService",
    "DetectorContext",
    "EvidenceRef",
    "ISSUE_STATUSES",
    "Issue",
    "IssueCluster",
    "IssueDetails",
    "IssueOccurrence",
    "Occurrence",
    "RuleDetector",
    "RuleFinding",
    "RuleRegistry",
    "RuleSpec",
    "default_rule_specs",
    "rule_specs",
]
