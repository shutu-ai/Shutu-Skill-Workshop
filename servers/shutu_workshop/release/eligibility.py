"""Pure-ish release eligibility checks; no file mutation happens here."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..evolution.evaluation import EvaluationService
from ..evolution.models import EvaluationReport, Proposal
from ..evolution.proposals import ProposalService
from ..models import utc_now
from ..storage import M1Database
from .artifacts import ArtifactManifest, artifact_tree_hash


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EligibilityDecision:
    decision: str
    reason_codes: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()
    proposal_id: str = ""
    proposal_version: int = 0
    issue_id: str = ""
    diagnosis_id: str = ""
    review_ref: dict[str, Any] = field(default_factory=dict)
    candidate_ref: dict[str, Any] = field(default_factory=dict)
    evaluation_ref: dict[str, Any] = field(default_factory=dict)
    suite_ref: dict[str, Any] = field(default_factory=dict)
    runner_ref: dict[str, Any] = field(default_factory=dict)
    oracle_ref: dict[str, Any] = field(default_factory=dict)
    manifest_ref: dict[str, Any] = field(default_factory=dict)
    baseline_artifact_hash: str = ""
    candidate_artifact_hash: str = ""
    target_current_hash: str = ""
    capability_version_id: str = ""
    verdict: str = ""
    evidence_tier: str = ""
    target_scope: str = ""
    evaluation_environment: str = ""
    approval_ref: dict[str, Any] = field(default_factory=dict)
    fixed_input_summary: dict[str, Any] = field(default_factory=dict)
    assessed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        value["evidence_gaps"] = list(self.evidence_gaps)
        return value


def _manifest(value: ArtifactManifest | dict[str, Any] | None) -> ArtifactManifest | None:
    if isinstance(value, ArtifactManifest):
        return value
    if isinstance(value, dict):
        try:
            return ArtifactManifest.from_dict(value)
        except (TypeError, KeyError):
            return None
    return None


class ReleaseEligibilityService:
    def __init__(self, workspace_or_db: str | Path | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.proposals = ProposalService(self.db)
        self.evaluations = EvaluationService(self.db)

    def assess(
        self,
        proposal_id: str,
        evaluation_run_id: str,
        candidate_artifact_ref: ArtifactManifest | dict[str, Any] | None,
        target_ref: str | Path | dict[str, Any],
        target_scope: str,
        approval_ref: str | dict[str, Any] | None = None,
    ) -> EligibilityDecision:
        proposal = self.proposals.get(proposal_id)
        report: EvaluationReport | None = None
        try:
            report = self.evaluations.report(evaluation_run_id)
        except ValueError:
            report = None
        manifest = _manifest(candidate_artifact_ref)
        candidate = manifest.to_dict() if manifest else {}
        target_path, target_current_hash = self._target(target_ref)
        run = report.run if report else {}
        verdict = report.verdict.verdict if report and report.verdict else str(run.get("verdict") or "")
        tier = report.verdict.evidence_tier if report and report.verdict else str(run.get("evidence_tier") or "")
        approval = approval_ref if isinstance(approval_ref, dict) else {}
        reasons: list[str] = []
        gaps: list[str] = []

        if proposal is None:
            reasons.append("PROPOSAL_NOT_FOUND")
        elif proposal.status not in {"evaluated"}:
            reasons.append("PROPOSAL_NOT_EVALUATED")
        if proposal and not proposal.reviewer:
            reasons.append("PROPOSAL_HUMAN_REVIEW_MISSING")
        if report is None:
            reasons.append("EVALUATION_NOT_FOUND")
        if manifest is None:
            reasons.append("CANDIDATE_MANIFEST_REQUIRED")
        if target_path is None:
            reasons.append("TARGET_NOT_FOUND")
        if target_scope not in {"isolated_workspace", "explicit_real_target"}:
            reasons.append("TARGET_SCOPE_UNSUPPORTED")
        if proposal and proposal.proposal_id != (manifest.proposal_id if manifest else ""):
            reasons.append("PROPOSAL_ARTIFACT_LINEAGE_MISMATCH")
        if proposal and manifest:
            if manifest.proposal_candidate_hash != proposal.candidate_hash:
                reasons.append("PROPOSAL_CANDIDATE_HASH_MISMATCH")
            if manifest.proposal_version != proposal.proposal_version:
                reasons.append("PROPOSAL_REVISION_MISMATCH")
            if manifest.capability_version_id != proposal.capability_version_id:
                reasons.append("CAPABILITY_VERSION_MISMATCH")
        if report and report.run.get("run_id") != evaluation_run_id:
            reasons.append("EVALUATION_ID_MISMATCH")
        if report and proposal and proposal.evaluation_id not in {"", evaluation_run_id}:
            reasons.append("PROPOSAL_EVALUATION_MISMATCH")
        if report and str(run.get("status")) != "completed":
            reasons.append("EVALUATION_NOT_COMPLETED")
        if verdict != "IMPROVED":
            reasons.append(f"VERDICT_NOT_RELEASEABLE:{verdict or 'UNKNOWN'}")
        required_tier = "REAL_TASK_EXECUTED" if target_scope == "explicit_real_target" else "ISOLATED_ARTIFACT_EXECUTED"
        if tier != required_tier:
            reasons.append(f"EVIDENCE_TIER_NOT_ALLOWED:{tier or 'UNKNOWN'}")
        if manifest and report:
            if manifest.candidate_artifact_hash != str(run.get("candidate_artifact_hash") or ""):
                reasons.append("CANDIDATE_ARTIFACT_HASH_MISMATCH")
            if manifest.baseline_artifact_hash != str(run.get("baseline_artifact_hash") or ""):
                reasons.append("BASELINE_ARTIFACT_HASH_MISMATCH")
            if manifest.manifest_hash == "" or not manifest.candidate_artifact_hash:
                reasons.append("MANIFEST_INCOMPLETE")
            try:
                if not self._verify_manifest(manifest):
                    reasons.append("CANDIDATE_ARTIFACT_MUTATED")
            except (OSError, ValueError):
                reasons.append("CANDIDATE_ARTIFACT_UNREADABLE")
        if manifest and target_current_hash and manifest.baseline_artifact_hash != target_current_hash:
            reasons.append("TARGET_DRIFT")

        if target_scope == "explicit_real_target":
            if not reasons and not approval_ref:
                gaps.append("human_approval_required")
                decision = "NEEDS_EVIDENCE"
            elif not reasons and not self._approval_matches(approval, proposal, report, manifest, target_current_hash):
                reasons.append("APPROVAL_BINDING_MISMATCH")
                decision = "DENIED"
            elif not reasons:
                decision = "CONDITIONAL_REAL_TARGET"
            else:
                decision = "DENIED"
        else:
            if not reasons and not approval_ref:
                gaps.append("human_approval_required")
                decision = "NEEDS_EVIDENCE"
            elif not reasons and not self._approval_matches(approval, proposal, report, manifest, target_current_hash):
                reasons.append("APPROVAL_BINDING_MISMATCH")
                decision = "DENIED"
            elif reasons:
                decision = "DENIED"
            else:
                decision = "ELIGIBLE_ISOLATED"

        if report and report.results:
            fixed_input_summary = {
                "sample_size": len({item.case_id for item in report.results}),
                "case_ids": sorted({item.case_id for item in report.results}),
                "unknown_cost": True,
            }
        else:
            fixed_input_summary = {"sample_size": 0, "unknown_cost": True}
        return EligibilityDecision(
            decision=decision,
            reason_codes=tuple(dict.fromkeys(reasons)),
            evidence_gaps=tuple(dict.fromkeys(gaps)),
            proposal_id=proposal.proposal_id if proposal else proposal_id,
            proposal_version=proposal.proposal_version if proposal else 0,
            issue_id=proposal.issue_id if proposal else "",
            diagnosis_id=proposal.diagnosis_id if proposal else "",
            review_ref={
                "reviewer": proposal.reviewer if proposal else "",
                "reviewed_at": proposal.reviewed_at if proposal else "",
                "status": proposal.status if proposal else "",
            },
            candidate_ref={
                "proposal_candidate_hash": manifest.proposal_candidate_hash if manifest else "",
                "artifact_root": manifest.candidate_root if manifest else "",
                "artifact_hash": manifest.candidate_artifact_hash if manifest else "",
                "patch_digest": manifest.patch_digest if manifest else "",
            },
            evaluation_ref={
                "run_id": evaluation_run_id,
                "status": run.get("status", ""),
                "verdict": verdict,
                "evidence_tier": tier,
                "environment_fingerprint": run.get("environment_fingerprint", ""),
            },
            suite_ref={
                "suite_id": run.get("suite_id", ""),
                "suite_hash": run.get("suite_hash", ""),
            },
            runner_ref={
                "runner_id": run.get("runner_id", ""),
                "runner_version": run.get("runner_version", ""),
                "config_hash": run.get("runner_config_hash", ""),
            },
            oracle_ref={"oracle_id": run.get("oracle_id", ""), "oracle_version": run.get("oracle_version", "")},
            manifest_ref={
                "manifest_hash": manifest.manifest_hash if manifest else "",
                "baseline_root": manifest.baseline_root if manifest else "",
                "candidate_root": manifest.candidate_root if manifest else "",
            },
            baseline_artifact_hash=manifest.baseline_artifact_hash if manifest else str(run.get("baseline_artifact_hash") or ""),
            candidate_artifact_hash=manifest.candidate_artifact_hash if manifest else str(run.get("candidate_artifact_hash") or ""),
            target_current_hash=target_current_hash,
            capability_version_id=proposal.capability_version_id if proposal else "",
            verdict=verdict,
            evidence_tier=tier,
            target_scope=target_scope,
            evaluation_environment=str(run.get("environment_fingerprint") or ""),
            approval_ref=approval,
            fixed_input_summary=fixed_input_summary,
            assessed_at=utc_now(),
        )

    @staticmethod
    def _target(target_ref: str | Path | dict[str, Any]) -> tuple[Path | None, str]:
        if isinstance(target_ref, dict):
            value = str(target_ref.get("root") or target_ref.get("path") or "")
            declared = str(target_ref.get("current_hash") or "")
        else:
            value = str(target_ref)
            declared = ""
        path = Path(value).resolve() if value else None
        if path is None or not path.is_dir():
            return None, declared
        try:
            actual = artifact_tree_hash(path)
        except (OSError, ValueError):
            return path, declared
        return path, actual

    @staticmethod
    def _verify_manifest(manifest: ArtifactManifest) -> bool:
        return artifact_tree_hash(manifest.baseline_root) == manifest.baseline_artifact_hash and artifact_tree_hash(manifest.candidate_root) == manifest.candidate_artifact_hash

    @staticmethod
    def _approval_matches(
        approval: dict[str, Any],
        proposal: Proposal | None,
        report: EvaluationReport | None,
        manifest: ArtifactManifest | None,
        target_hash: str,
    ) -> bool:
        if not approval.get("reviewer") or not approval.get("approved_at"):
            return False
        return bool(
            proposal
            and report
            and manifest
            and approval.get("proposal_id", proposal.proposal_id) == proposal.proposal_id
            and approval.get("evaluation_run_id", report.run.get("run_id")) == report.run.get("run_id")
            and approval.get("candidate_artifact_hash") == manifest.candidate_artifact_hash
            and approval.get("target_current_hash") == target_hash
            and approval.get("evaluation_manifest_hash") == report.run.get("execution_manifest_hash")
        )


def assess_release_eligibility(
    proposal_id: str,
    evaluation_run_id: str,
    candidate_artifact_ref: ArtifactManifest | dict[str, Any] | None,
    target_ref: str | Path | dict[str, Any],
    target_scope: str,
    approval_ref: str | dict[str, Any] | None = None,
    *,
    workspace_or_db: str | Path | M1Database | None = None,
) -> EligibilityDecision:
    if workspace_or_db is None:
        # A standalone decision can still be made from serialized evidence, but
        # proposal/evaluation lineage is unavailable and will be reported as a gap.
        return EligibilityDecision(
            decision="NEEDS_EVIDENCE",
            reason_codes=("WORKSPACE_REQUIRED_FOR_LINEAGE",),
            evidence_gaps=("workspace_or_db",),
            proposal_id=proposal_id,
            evaluation_ref={"run_id": evaluation_run_id},
            target_scope=target_scope,
            assessed_at=utc_now(),
        )
    return ReleaseEligibilityService(workspace_or_db).assess(
        proposal_id,
        evaluation_run_id,
        candidate_artifact_ref,
        target_ref,
        target_scope,
        approval_ref,
    )


__all__ = ["EligibilityDecision", "ReleaseEligibilityService", "assess_release_eligibility"]
