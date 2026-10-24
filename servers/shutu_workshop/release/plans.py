"""Durable ReleasePlan, approval, M0 link, and recovery operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..evolution.models import Proposal
from ..evolution.proposals import ProposalService
from ..models import new_id, utc_now
from ..storage import M1Database
from .artifacts import ArtifactError, ArtifactManifest, FileArtifactService, artifact_tree_hash
from .eligibility import EligibilityDecision, ReleaseEligibilityService
from .m0_bridge import M0ReleaseBridge


class ReleasePlanError(RuntimeError):
    """Raised when a plan cannot safely advance or be executed."""


class ReleaseInProgress(ReleasePlanError):
    """A different process owns the current plan execution."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReleasePlan:
    plan_id: str
    proposal_id: str
    evaluation_run_id: str
    target_scope: str
    target_skill_name: str
    target_root: str
    candidate_root: str
    baseline_root: str
    baseline_artifact_hash: str
    candidate_artifact_hash: str
    target_current_hash: str
    evaluation_manifest_hash: str
    state: str
    plan_hash: str
    eligibility: dict[str, Any]
    artifact_manifest: dict[str, Any]
    proposal: dict[str, Any]
    created_at: str
    updated_at: str
    row_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReleasePlan":
        return cls(**dict(value))


@dataclass(frozen=True)
class ReleaseApproval:
    approval_id: str
    plan_id: str
    reviewer: str
    approved_at: str
    expected_plan_hash: str
    candidate_artifact_hash: str
    target_current_hash: str
    evaluation_manifest_hash: str
    status: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseLink:
    release_id: str
    plan_id: str
    idempotency_key: str
    status: str
    target_scope: str
    target_skill_name: str
    candidate_artifact_hash: str
    baseline_artifact_hash: str
    target_current_hash: str
    capability_version_id: str
    m0_operation_id: str
    m0_version_id: str
    rollback_version_id: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReleasePlanService:
    STATES = frozenset({"draft", "eligible", "approved", "executing", "completed", "failed", "recovering", "rolled_back"})

    def __init__(self, workspace_or_db: str | Path | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.workspace = self.db.db_path.parent.parent
        self.proposals = ProposalService(self.db)
        self.eligibility = ReleaseEligibilityService(self.db)

    def create_plan(
        self,
        proposal_id: str,
        evaluation_run_id: str,
        manifest: ArtifactManifest | dict[str, Any],
        isolated_root: str | Path,
        *,
        target_skill_name: str | None = None,
        target_scope: str = "isolated_workspace",
    ) -> ReleasePlan:
        artifact = manifest if isinstance(manifest, ArtifactManifest) else ArtifactManifest.from_dict(manifest)
        isolated = Path(isolated_root).resolve()
        target_name = target_skill_name or (Path(artifact.target_root).name if artifact.target_root else Path(artifact.baseline_root).name)
        target = (isolated / target_name).resolve()
        candidate = Path(artifact.candidate_root).resolve()
        self._assert_isolated(target, isolated)
        self._assert_isolated(candidate, isolated)
        self._assert_isolated(Path(artifact.baseline_root).resolve(), isolated)
        if not FileArtifactService.verify(artifact):
            raise ReleasePlanError("candidate manifest no longer matches its files")
        target_hash = artifact_tree_hash(target)
        if target_hash != artifact.baseline_artifact_hash:
            raise ReleasePlanError("isolated target drifted before planning")
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise ReleasePlanError("proposal does not exist")
        decision = self.eligibility.assess(
            proposal_id,
            evaluation_run_id,
            artifact,
            {"root": str(target), "current_hash": target_hash},
            target_scope,
        )
        plan_core = {
            "proposal_id": proposal_id,
            "evaluation_run_id": evaluation_run_id,
            "target_scope": target_scope,
            "target_skill_name": target_name,
            "target_root": str(target),
            "candidate_root": str(candidate),
            "baseline_root": str(artifact.baseline_root),
            "baseline_artifact_hash": artifact.baseline_artifact_hash,
            "candidate_artifact_hash": artifact.candidate_artifact_hash,
            "target_current_hash": target_hash,
            "evaluation_manifest_hash": self._evaluation_manifest_hash(evaluation_run_id),
            "artifact_manifest_hash": artifact.manifest_hash,
        }
        plan_hash = _digest(plan_core)
        with self.db.transaction() as connection:
            existing = connection.execute("SELECT plan_json FROM release_plans WHERE plan_hash = ?", (plan_hash,)).fetchone()
            if existing:
                return ReleasePlan.from_dict(json.loads(existing[0]))
            now = utc_now()
            plan = ReleasePlan(
                plan_id=new_id("plan"),
                proposal_id=proposal_id,
                evaluation_run_id=evaluation_run_id,
                target_scope=target_scope,
                target_skill_name=target_name,
                target_root=str(target),
                candidate_root=str(candidate),
                baseline_root=str(artifact.baseline_root),
                baseline_artifact_hash=artifact.baseline_artifact_hash,
                candidate_artifact_hash=artifact.candidate_artifact_hash,
                target_current_hash=target_hash,
                evaluation_manifest_hash=plan_core["evaluation_manifest_hash"],
                state="eligible" if not decision.reason_codes else "draft",
                plan_hash=plan_hash,
                eligibility=decision.to_dict(),
                artifact_manifest=artifact.to_dict(),
                proposal=proposal.to_dict(),
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                """
                INSERT INTO release_plans(
                    plan_id, proposal_id, evaluation_run_id, target_scope, target_skill_name,
                    target_root, candidate_root, baseline_root, baseline_artifact_hash,
                    candidate_artifact_hash, target_current_hash, evaluation_manifest_hash,
                    state, plan_hash, plan_json, created_at, updated_at, row_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    plan.plan_id,
                    plan.proposal_id,
                    plan.evaluation_run_id,
                    plan.target_scope,
                    plan.target_skill_name,
                    plan.target_root,
                    plan.candidate_root,
                    plan.baseline_root,
                    plan.baseline_artifact_hash,
                    plan.candidate_artifact_hash,
                    plan.target_current_hash,
                    plan.evaluation_manifest_hash,
                    plan.state,
                    plan.plan_hash,
                    _canonical(plan.to_dict()),
                    plan.created_at,
                    plan.updated_at,
                ),
            )
            self._event(connection, plan.plan_id, "", plan.state, "plan_created", {"eligibility": decision.to_dict()})
            return plan

    def get_plan(self, plan_id: str) -> ReleasePlan | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT plan_json FROM release_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        return ReleasePlan.from_dict(json.loads(row[0])) if row else None

    def approve(self, plan_id: str, *, reviewer: str = "human", expected_plan_hash: str) -> ReleaseApproval:
        plan = self._require_plan(plan_id)
        if plan.plan_hash != expected_plan_hash:
            raise ReleasePlanError("plan hash changed; re-plan before approval")
        if plan.state not in {"eligible", "draft"}:
            raise ReleasePlanError(f"plan cannot be approved from state {plan.state}")
        decision = self._verify_decision(plan, approval=None)
        if decision.reason_codes:
            raise ReleasePlanError("; ".join(decision.reason_codes))
        now = utc_now()
        approval_payload = {
            "proposal_id": plan.proposal_id,
            "evaluation_run_id": plan.evaluation_run_id,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "candidate_artifact_hash": plan.candidate_artifact_hash,
            "target_current_hash": plan.target_current_hash,
            "evaluation_manifest_hash": plan.evaluation_manifest_hash,
            "reviewer": reviewer,
            "approved_at": now,
        }
        approval = ReleaseApproval(
            approval_id=new_id("approval"),
            plan_id=plan.plan_id,
            reviewer=str(reviewer or "").strip(),
            approved_at=now,
            expected_plan_hash=expected_plan_hash,
            candidate_artifact_hash=plan.candidate_artifact_hash,
            target_current_hash=plan.target_current_hash,
            evaluation_manifest_hash=plan.evaluation_manifest_hash,
            status="approved",
            payload=approval_payload,
        )
        if not approval.reviewer:
            raise ReleasePlanError("reviewer is required")
        with self.db.transaction() as connection:
            row = connection.execute("SELECT row_version, state FROM release_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ReleasePlanError("plan does not exist")
            if int(row["row_version"]) != plan.row_version or row["state"] not in {"eligible", "draft"}:
                raise ReleasePlanError("plan changed concurrently")
            connection.execute(
                """
                INSERT INTO release_approvals(
                    approval_id, plan_id, reviewer, approved_at, expected_plan_hash,
                    candidate_artifact_hash, target_current_hash, evaluation_manifest_hash,
                    status, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.plan_id,
                    approval.reviewer,
                    approval.approved_at,
                    approval.expected_plan_hash,
                    approval.candidate_artifact_hash,
                    approval.target_current_hash,
                    approval.evaluation_manifest_hash,
                    approval.status,
                    _canonical(approval.payload),
                ),
            )
            updated = replace(
                plan,
                state="approved",
                eligibility=replace(decision, approval_ref=approval.payload, decision="ELIGIBLE_ISOLATED").to_dict(),
                updated_at=now,
                row_version=plan.row_version + 1,
            )
            connection.execute(
                "UPDATE release_plans SET state = ?, plan_json = ?, updated_at = ?, row_version = ? WHERE plan_id = ? AND row_version = ?",
                (updated.state, _canonical(updated.to_dict()), updated.updated_at, updated.row_version, plan_id, plan.row_version),
            )
            self._event(connection, plan_id, plan.state, updated.state, "human_approved", approval.payload)
        return approval

    def verify(self, plan_id: str, *, isolated_root: str | Path | None = None) -> EligibilityDecision:
        plan = self._require_plan(plan_id)
        if isolated_root is not None:
            self._assert_isolated(Path(plan.target_root), Path(isolated_root).resolve())
            self._assert_isolated(Path(plan.candidate_root), Path(isolated_root).resolve())
        if plan.state != "approved":
            raise ReleasePlanError(f"plan is not approved: {plan.state}")
        approval = self._approval(plan.plan_id)
        decision = self._verify_decision(plan, approval.payload if approval else None)
        if decision.decision != "ELIGIBLE_ISOLATED":
            raise ReleasePlanError("release verification denied: " + "; ".join(decision.reason_codes or decision.evidence_gaps))
        return decision

    def execute(self, plan_id: str, *, isolated_root: str | Path | None = None) -> ReleaseLink:
        plan = self._require_plan(plan_id)
        existing = self._link_for_plan(plan_id)
        if existing and existing.status == "completed":
            return existing
        if existing and existing.status == "executing":
            reconciled = self.reconcile(plan_id)
            if reconciled and reconciled.status == "completed":
                return reconciled
            raise ReleaseInProgress(f"release is already executing: {plan_id}")
        self.verify(plan_id, isolated_root=isolated_root)
        idempotency_key = _digest(
            {
                "plan_id": plan.plan_id,
                "target_identity": str(Path(plan.target_root).resolve()),
                "candidate_hash": plan.candidate_artifact_hash,
                "expected_base_hash": plan.target_current_hash,
            }
        )
        release_id = new_id("release")
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute("SELECT state, row_version FROM release_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row is None or row["state"] != "approved" or int(row["row_version"]) != plan.row_version:
                link = self._link_for_plan(plan_id)
                if link and link.status == "completed":
                    return link
                raise ReleaseInProgress("plan changed while execute was being claimed")
            connection.execute(
                "UPDATE release_plans SET state = 'executing', updated_at = ?, row_version = row_version + 1 WHERE plan_id = ? AND row_version = ?",
                (now, plan_id, plan.row_version),
            )
            connection.execute(
                """
                INSERT INTO release_links(
                    release_id, plan_id, idempotency_key, status, target_scope, target_skill_name,
                    candidate_artifact_hash, baseline_artifact_hash, target_current_hash,
                    capability_version_id, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, 'executing', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release_id,
                    plan_id,
                    idempotency_key,
                    plan.target_scope,
                    plan.target_skill_name,
                    plan.candidate_artifact_hash,
                    plan.baseline_artifact_hash,
                    plan.target_current_hash,
                    str(plan.proposal.get("capability_version_id") or ""),
                    now,
                    now,
                    _canonical({"plan_id": plan_id, "plan_hash": plan.plan_hash}),
                ),
            )
            self._event(connection, plan_id, plan.state, "executing", "release_claimed", {"release_id": release_id})
        plan = self._require_plan(plan_id)
        manifest = ArtifactManifest.from_dict(plan.artifact_manifest)
        bridge = M0ReleaseBridge(self.workspace, Path(plan.target_root).parent)
        try:
            result = bridge.publish(plan, manifest)
        except BaseException as exc:
            recovered = self.reconcile(plan_id)
            if recovered and recovered.status == "completed":
                return recovered
            self._mark_failed(plan_id, release_id, f"{type(exc).__name__}: {exc}")
            raise
        self._mark_completed(plan_id, release_id, result.operation_id, result.version.id, result.version.parent_version_id)
        completed = self._link_for_plan(plan_id)
        if completed is None:
            raise ReleasePlanError("release completed without a persisted link")
        return completed

    def reconcile(self, plan_id: str) -> ReleaseLink | None:
        plan = self._require_plan(plan_id)
        link = self._link_for_plan(plan_id)
        if link is None or link.status != "executing":
            return link
        from plugins.daagent.skill_workshop.engine import SkillWorkshop

        workshop = SkillWorkshop(self.workspace, skills_dir=Path(plan.target_root).parent)
        operations = [
            operation
            for operation in workshop.store.list_release_operations()
            if operation.target_skill_name == plan.target_skill_name
            and operation.candidate_hash == plan.candidate_artifact_hash
            and plan.plan_id in operation.message
            and operation.status == "completed"
        ]
        if not operations or artifact_tree_hash(plan.target_root) != plan.candidate_artifact_hash:
            return link
        operation = operations[-1]
        version = workshop.store.get_version(operation.target_version_id)
        if version is None:
            return link
        self._mark_completed(plan_id, link.release_id, operation.operation_id, version.id, version.parent_version_id)
        return self._link_for_plan(plan_id)

    def rollback(self, release_id: str, version_id: str) -> ReleaseLink:
        link = self._link_by_id(release_id)
        if link is None:
            raise ReleasePlanError("release does not exist")
        if link.status == "rolled_back":
            return link
        if link.status != "completed":
            raise ReleasePlanError(f"release cannot be rolled back from state {link.status}")
        plan = self._require_plan(link.plan_id)
        from plugins.daagent.skill_workshop.engine import SkillWorkshop

        workshop = SkillWorkshop(self.workspace, skills_dir=Path(plan.target_root).parent)
        selected = workshop.store.get_version(version_id)
        if selected is None or selected.package_hash != plan.baseline_artifact_hash:
            raise ReleasePlanError("rollback version is not the verified baseline")
        result = M0ReleaseBridge(self.workspace, Path(plan.target_root).parent).rollback(plan, version_id)
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE release_links SET status = 'rolled_back', rollback_version_id = ?, m0_operation_id = ?, m0_version_id = ?, updated_at = ?, payload_json = ? WHERE release_id = ? AND status = 'completed'",
                (
                    result.version.id,
                    result.operation_id,
                    link.m0_version_id,
                    now,
                    _canonical({**link.payload, "rollback_version_id": result.version.id, "rollback_hash": result.version.package_hash}),
                    release_id,
                ),
            )
            plan_row = connection.execute("SELECT plan_json, row_version, state FROM release_plans WHERE plan_id = ?", (plan.plan_id,)).fetchone()
            if plan_row and plan_row["state"] == "completed":
                current = ReleasePlan.from_dict(json.loads(plan_row["plan_json"]))
                updated = replace(current, state="rolled_back", updated_at=now, row_version=int(plan_row["row_version"]) + 1)
                connection.execute("UPDATE release_plans SET state = ?, plan_json = ?, updated_at = ?, row_version = ? WHERE plan_id = ? AND row_version = ?", (updated.state, _canonical(updated.to_dict()), now, updated.row_version, plan.plan_id, int(plan_row["row_version"])))
                self._event(connection, plan.plan_id, "completed", "rolled_back", "human_rollback", {"version_id": version_id, "m0_operation_id": result.operation_id})
        value = self._link_by_id(release_id)
        if value is None:
            raise ReleasePlanError("rollback link disappeared")
        return value

    def status(self, plan_id: str) -> dict[str, Any]:
        plan = self._require_plan(plan_id)
        link = self._link_for_plan(plan_id)
        return {"plan": plan.to_dict(), "approval": self._approval(plan_id).to_dict() if self._approval(plan_id) else None, "release": link.to_dict() if link else None}

    def _verify_decision(self, plan: ReleasePlan, approval: dict[str, Any] | None) -> EligibilityDecision:
        artifact = ArtifactManifest.from_dict(plan.artifact_manifest)
        return self.eligibility.assess(
            plan.proposal_id,
            plan.evaluation_run_id,
            artifact,
            {"root": plan.target_root, "current_hash": artifact_tree_hash(plan.target_root)},
            plan.target_scope,
            approval,
        )

    def _evaluation_manifest_hash(self, evaluation_run_id: str) -> str:
        with self.db.connection() as connection:
            row = connection.execute("SELECT execution_manifest_hash FROM evaluation_runs WHERE run_id = ?", (evaluation_run_id,)).fetchone()
        return str(row[0]) if row else ""

    def _require_plan(self, plan_id: str) -> ReleasePlan:
        plan = self.get_plan(plan_id)
        if plan is None:
            raise ReleasePlanError(f"plan does not exist: {plan_id}")
        return plan

    def _approval(self, plan_id: str) -> ReleaseApproval | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM release_approvals WHERE plan_id = ? ORDER BY approved_at DESC LIMIT 1", (plan_id,)).fetchone()
        if row is None:
            return None
        return ReleaseApproval(
            approval_id=row["approval_id"],
            plan_id=row["plan_id"],
            reviewer=row["reviewer"],
            approved_at=row["approved_at"],
            expected_plan_hash=row["expected_plan_hash"],
            candidate_artifact_hash=row["candidate_artifact_hash"],
            target_current_hash=row["target_current_hash"],
            evaluation_manifest_hash=row["evaluation_manifest_hash"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
        )

    def _link_for_plan(self, plan_id: str) -> ReleaseLink | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM release_links WHERE plan_id = ?", (plan_id,)).fetchone()
        return self._link(row) if row else None

    def _link_by_id(self, release_id: str) -> ReleaseLink | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM release_links WHERE release_id = ?", (release_id,)).fetchone()
        return self._link(row) if row else None

    @staticmethod
    def _link(row: Any) -> ReleaseLink:
        return ReleaseLink(
            release_id=row["release_id"],
            plan_id=row["plan_id"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            target_scope=row["target_scope"],
            target_skill_name=row["target_skill_name"],
            candidate_artifact_hash=row["candidate_artifact_hash"],
            baseline_artifact_hash=row["baseline_artifact_hash"],
            target_current_hash=row["target_current_hash"],
            capability_version_id=row["capability_version_id"],
            m0_operation_id=row["m0_operation_id"],
            m0_version_id=row["m0_version_id"],
            rollback_version_id=row["rollback_version_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _mark_completed(self, plan_id: str, release_id: str, operation_id: str, version_id: str, baseline_version_id: str) -> None:
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE release_links SET status = 'completed', m0_operation_id = ?, m0_version_id = ?, updated_at = ?, payload_json = json_set(payload_json, '$.baseline_version_id', ?) WHERE release_id = ? AND status = 'executing'",
                (operation_id, version_id, now, baseline_version_id, release_id),
            )
            row = connection.execute("SELECT plan_json, row_version, state FROM release_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row and row["state"] == "executing":
                current = ReleasePlan.from_dict(json.loads(row["plan_json"]))
                updated = replace(current, state="completed", updated_at=now, row_version=int(row["row_version"]) + 1)
                connection.execute("UPDATE release_plans SET state = ?, plan_json = ?, updated_at = ?, row_version = ? WHERE plan_id = ? AND row_version = ?", (updated.state, _canonical(updated.to_dict()), now, updated.row_version, plan_id, int(row["row_version"])))
                self._event(connection, plan_id, "executing", "completed", "m0_release_completed", {"operation_id": operation_id, "version_id": version_id})

    def _mark_failed(self, plan_id: str, release_id: str, error: str) -> None:
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute("UPDATE release_links SET status = 'failed', updated_at = ?, payload_json = ? WHERE release_id = ? AND status = 'executing'", (now, _canonical({"error": error}), release_id))
            row = connection.execute("SELECT plan_json, row_version, state FROM release_plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if row and row["state"] == "executing":
                current = ReleasePlan.from_dict(json.loads(row["plan_json"]))
                updated = replace(current, state="failed", updated_at=now, row_version=int(row["row_version"]) + 1)
                connection.execute("UPDATE release_plans SET state = ?, plan_json = ?, updated_at = ?, row_version = ? WHERE plan_id = ? AND row_version = ?", (updated.state, _canonical(updated.to_dict()), now, updated.row_version, plan_id, int(row["row_version"])))
                self._event(connection, plan_id, "executing", "failed", "release_failed", {"error": error})

    @staticmethod
    def _assert_isolated(path: Path, root: Path) -> None:
        if path.exists() and path.is_symlink():
            raise ReleasePlanError("symlink path is not allowed")
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ReleasePlanError("release path escapes isolated root") from exc

    @staticmethod
    def _event(connection: Any, plan_id: str, old_state: str, new_state: str, reason: str, evidence: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO release_plan_events(event_id, plan_id, old_state, new_state, reason, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("release-event"), plan_id, old_state, new_state, reason, _canonical(evidence), utc_now()),
        )


__all__ = [
    "ReleaseApproval",
    "ReleaseInProgress",
    "ReleaseLink",
    "ReleasePlan",
    "ReleasePlanError",
    "ReleasePlanService",
]
