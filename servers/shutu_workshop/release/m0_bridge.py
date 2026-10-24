"""Thin bridge from an M5 structured file patch to the existing M0 journal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..evolution.models import Proposal
from plugins.daagent.skill_workshop.engine import SkillWorkshop
from plugins.daagent.skill_workshop.models import Patch, Version
from .artifacts import ArtifactError, ArtifactManifest, FileArtifactService, artifact_tree_hash


@dataclass(frozen=True)
class M0ReleaseResult:
    version: Version
    operation_id: str
    m0_proposal_id: str


class M0ReleaseBridge:
    """Convert only a reviewed file patch, then delegate all mutation to M0."""

    def __init__(self, workspace: str | Path, isolated_root: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.isolated_root = Path(isolated_root).resolve()
        self.workshop = SkillWorkshop(self.workspace, skills_dir=self.isolated_root)

    def publish(self, plan: Any, manifest: ArtifactManifest) -> M0ReleaseResult:
        target_name = str(plan.target_skill_name)
        target = (self.isolated_root / target_name).resolve()
        if not self._within(target, self.isolated_root) or not target.is_dir():
            raise ArtifactError("M5 target is outside the isolated Skill root")
        if not FileArtifactService.verify(manifest):
            raise ArtifactError("M5 candidate or baseline artifact changed before publish")
        if artifact_tree_hash(target) != manifest.baseline_artifact_hash:
            raise ArtifactError("M5 target drifted before M0 bridge")
        proposal_value = self._proposal_value(plan)
        patch_payload = FileArtifactService.patch_payload(proposal_value)
        if FileArtifactService._patch_digest(patch_payload) != manifest.patch_digest:
            raise ArtifactError("M5 patch digest differs from the reviewed artifact manifest")
        patches = [
            Patch(
                path=item["path"],
                old_string=item["old_string"],
                new_string=item["new_string"],
                replace_all=bool(item.get("replace_all", False)),
                reason=str(item.get("reason") or "M5 reviewed file patch"),
            )
            for item in patch_payload
        ]
        m0_proposal = self.workshop.create_proposal(
            target_name,
            reason=f"M5 plan {plan.plan_id}: reviewed isolated Skill release",
            changes=patches,
        )
        if m0_proposal.base_hash != manifest.baseline_artifact_hash:
            raise ArtifactError("M0 proposal base hash differs from M5 baseline")
        self.workshop.accept_all(m0_proposal.id)
        report = self.workshop.test_proposal(m0_proposal.id)
        if not report.get("passed") or report.get("candidate_hash") != manifest.candidate_artifact_hash:
            raise ArtifactError("M0 test fingerprint does not match M5 candidate artifact")
        version = self.workshop.apply_proposal(m0_proposal.id)
        if version.package_hash != manifest.candidate_artifact_hash or artifact_tree_hash(target) != manifest.candidate_artifact_hash:
            raise ArtifactError("M0 published bytes do not match M5 candidate artifact")
        operations = [item for item in self.workshop.store.list_release_operations() if item.target_version_id == version.id]
        if not operations:
            raise ArtifactError("M0 release journal operation was not persisted")
        return M0ReleaseResult(version, operations[-1].operation_id, m0_proposal.id)

    def rollback(self, plan: Any, version_id: str) -> M0ReleaseResult:
        version = self.workshop.rollback(plan.target_skill_name, version_id)
        if artifact_tree_hash(Path(plan.target_root)) != version.package_hash:
            raise ArtifactError("M0 rollback bytes do not match the selected version")
        operations = [item for item in self.workshop.store.list_release_operations() if item.target_version_id == version.id]
        if not operations:
            raise ArtifactError("M0 rollback journal operation was not persisted")
        return M0ReleaseResult(version, operations[-1].operation_id, "")

    @staticmethod
    def _proposal_value(plan: Any) -> dict[str, Any]:
        value = getattr(plan, "proposal", None)
        if isinstance(value, dict):
            return value
        return dict(getattr(plan, "proposal_json", {}) or {})

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False


__all__ = ["M0ReleaseBridge", "M0ReleaseResult"]
