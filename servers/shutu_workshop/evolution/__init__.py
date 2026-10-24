"""M4 evidence-gated diagnosis, proposal review, and evaluation."""

from .diagnosis import DiagnosisAdvisor, DiagnosisOutputError, EvolutionDiagnosisService
from .evaluation import EvaluationService, StaticValidator, local_fixture_suite
from .evidence import EvidenceBundleBuilder, EvidenceValidationError, validate_evidence_refs
from .models import (
    DiagnosisHypothesis,
    DiagnosisReport,
    EvaluationCandidate,
    EvaluationCaseResult,
    EvaluationReport,
    EvaluationSuite,
    EvaluationVerdict,
    EvidenceBundle,
    Proposal,
    RecommendedAction,
    TaskCase,
)
from .proposals import PROPOSAL_CATEGORIES, PROPOSAL_STATUSES, ProposalService
from .providers import (
    DiagnosisProvider,
    FakeProvider,
    HTTPDiagnosisProvider,
    LLMClient,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailable,
    provider_from_name,
)

__all__ = [
    "DiagnosisAdvisor",
    "DiagnosisHypothesis",
    "DiagnosisOutputError",
    "DiagnosisProvider",
    "DiagnosisReport",
    "EvaluationCandidate",
    "EvaluationCaseResult",
    "EvaluationReport",
    "EvaluationService",
    "EvaluationSuite",
    "EvaluationVerdict",
    "EvidenceBundle",
    "EvidenceBundleBuilder",
    "EvidenceValidationError",
    "EvolutionDiagnosisService",
    "FakeProvider",
    "HTTPDiagnosisProvider",
    "LLMClient",
    "PROPOSAL_CATEGORIES",
    "PROPOSAL_STATUSES",
    "Proposal",
    "ProposalService",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderUnavailable",
    "RecommendedAction",
    "StaticValidator",
    "TaskCase",
    "local_fixture_suite",
    "provider_from_name",
    "validate_evidence_refs",
]
