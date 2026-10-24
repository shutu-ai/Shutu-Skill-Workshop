"""Human review and immutable candidate lineage for diagnosis actions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..capabilities import CapabilityRegistry
from ..diagnostics import ConcurrentUpdateError
from ..models import new_id, utc_now
from ..storage import M1Database
from .diagnosis import EvolutionDiagnosisService
from .evidence import EvidenceBundleBuilder
from .models import DiagnosisReport, Proposal


PROPOSAL_CATEGORIES = frozenset(
    {
        "skill_instruction",
        "skill_script",
        "mcp_description",
        "mcp_schema",
        "mcp_implementation_advisory",
        "agent_policy_advisory",
        "environment_action",
    }
)
PROPOSAL_STATUSES = frozenset(
    {"draft", "ready_for_review", "accepted_for_evaluation", "rejected", "needs_revision", "evaluated", "stale", "superseded"}
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ProposalService:
    def __init__(self, workspace_or_db: str | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.diagnoses = EvolutionDiagnosisService(self.db)
        self.builder = EvidenceBundleBuilder(self.db)
        self.capabilities = CapabilityRegistry(self.db)

    def create_from_diagnosis(self, issue_id: str, diagnosis_id: str | None = None) -> list[Proposal]:
        report = self.diagnoses.get(diagnosis_id) if diagnosis_id else None
        if report is None:
            reports = self.diagnoses.list(issue_id)
            report = reports[-1] if reports else self.diagnoses.diagnose(issue_id, provider="fake")
        if report.issue_id != issue_id:
            raise ValueError("diagnosis does not belong to issue")
        if not report.recommended_actions:
            return []
        bundle = self.builder.build(issue_id)
        result: list[Proposal] = []
        for action in report.recommended_actions:
            category = action.target_type
            if category not in PROPOSAL_CATEGORIES:
                raise ValueError(f"unsupported proposal category: {category}")
            capability_id = action.target_capability_id or str(bundle.issue.get("primary_capability_id") or "")
            version = next(
                (item for item in bundle.capability_versions if item.get("capability_id") == capability_id),
                None,
            )
            capability_version_id = str(version.get("version_id") or "") if version else ""
            target_hash = _digest(
                {
                    "capability_id": capability_id,
                    "capability_version_id": capability_version_id,
                    "content_hash": version.get("content_hash") if version else "",
                    "schema_hash": version.get("schema_hash") if version else "",
                }
            )
            payload = action.to_dict()
            candidate_hash = _digest(
                {
                    "issue_id": issue_id,
                    "diagnosis_id": report.diagnosis_id,
                    "evidence_bundle_hash": report.evidence_bundle_hash,
                    "proposal_version": 1,
                    "target_hash": target_hash,
                    "payload": payload,
                }
            )
            existing = self._find_candidate(issue_id, report.diagnosis_id, candidate_hash)
            if existing is not None:
                result.append(existing)
                continue
            now = utc_now()
            proposal = Proposal(
                proposal_id=new_id("proposal"),
                issue_id=issue_id,
                diagnosis_id=report.diagnosis_id,
                evidence_bundle_hash=report.evidence_bundle_hash,
                capability_id=capability_id,
                capability_version_id=capability_version_id,
                target_hash=target_hash,
                category=category,
                proposal_version=1,
                status="ready_for_review",
                payload=payload,
                candidate_hash=candidate_hash,
                reviewer="",
                reviewed_at="",
                evaluation_id="",
                stale_reason="",
                created_at=now,
                updated_at=now,
                row_version=0,
            )
            try:
                with self.db.transaction() as connection:
                    connection.execute(
                        """
                        INSERT INTO evolution_proposals(
                            proposal_id, issue_id, diagnosis_id, evidence_bundle_hash,
                            capability_id, capability_version_id, target_hash, category,
                            proposal_version, status, payload_json, candidate_hash,
                            reviewer, reviewed_at, evaluation_id, stale_reason,
                            created_at, updated_at, row_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            proposal.proposal_id,
                            proposal.issue_id,
                            proposal.diagnosis_id,
                            proposal.evidence_bundle_hash,
                            proposal.capability_id,
                            proposal.capability_version_id,
                            proposal.target_hash,
                            proposal.category,
                            proposal.proposal_version,
                            proposal.status,
                            _canonical(proposal.payload),
                            proposal.candidate_hash,
                            proposal.reviewer,
                            proposal.reviewed_at,
                            proposal.evaluation_id,
                            proposal.stale_reason,
                            proposal.created_at,
                            proposal.updated_at,
                        ),
                    )
            except sqlite3.IntegrityError:
                cached = self._find_candidate(issue_id, report.diagnosis_id, candidate_hash)
                if cached is None:
                    raise
                proposal = cached
            result.append(proposal)
        return result

    def get(self, proposal_id: str) -> Proposal | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT * FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return self._from_row(row) if row else None

    show = get

    def list(self, issue_id: str | None = None, *, status: str | None = None) -> list[Proposal]:
        query = "SELECT * FROM evolution_proposals"
        conditions: list[str] = []
        values: list[Any] = []
        if issue_id:
            conditions.append("issue_id = ?")
            values.append(issue_id)
        if status:
            conditions.append("status = ?")
            values.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at, proposal_id"
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._from_row(row) for row in rows]

    def review(
        self,
        proposal_id: str,
        decision: str,
        *,
        reviewer: str = "human",
        expected_row_version: int | None = None,
    ) -> Proposal:
        decision = decision.replace("-", "_")
        target = {
            "ready_for_review": "ready_for_review",
            "accept_for_eval": "accepted_for_evaluation",
            "accepted_for_evaluation": "accepted_for_evaluation",
            "reject": "rejected",
            "rejected": "rejected",
            "needs_revision": "needs_revision",
        }.get(decision)
        if target is None:
            raise ValueError(f"unsupported proposal decision: {decision}")
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
            if row is None:
                raise ValueError("proposal does not exist")
            current = self._from_row(row)
            if expected_row_version is not None and expected_row_version != current.row_version:
                raise ConcurrentUpdateError("proposal row_version changed")
            allowed = {
                "draft": {"ready_for_review"},
                "ready_for_review": {"accepted_for_evaluation", "rejected", "needs_revision", "ready_for_review"},
                "needs_revision": {"ready_for_review"},
            }
            if target not in allowed.get(current.status, set()):
                raise ValueError(f"invalid proposal transition: {current.status} -> {target}")
            now = utc_now()
            new_version = current.row_version + (0 if current.status == target else 1)
            connection.execute(
                """
                UPDATE evolution_proposals
                SET status = ?, reviewer = ?, reviewed_at = ?, updated_at = ?, row_version = ?
                WHERE proposal_id = ? AND row_version = ?
                """,
                (target, reviewer, now, now, new_version, proposal_id, current.row_version),
            )
        value = self.get(proposal_id)
        if value is None:
            raise RuntimeError("proposal disappeared after review")
        return value

    def revise(
        self,
        proposal_id: str,
        payload: dict[str, Any],
        *,
        expected_row_version: int | None = None,
    ) -> Proposal:
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM evolution_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
            if row is None:
                raise ValueError("proposal does not exist")
            current = self._from_row(row)
            if expected_row_version is not None and expected_row_version != current.row_version:
                raise ConcurrentUpdateError("proposal row_version changed")
            target_hash = _digest({"capability_id": current.capability_id, "capability_version_id": current.capability_version_id, "payload": payload})
            candidate_hash = _digest(
                {
                    "issue_id": current.issue_id,
                    "diagnosis_id": current.diagnosis_id,
                    "evidence_bundle_hash": current.evidence_bundle_hash,
                    "proposal_version": current.proposal_version + 1,
                    "target_hash": target_hash,
                    "payload": payload,
                }
            )
            now = utc_now()
            connection.execute(
                """
                UPDATE evolution_proposals
                SET target_hash = ?, proposal_version = ?, status = 'ready_for_review',
                    payload_json = ?, candidate_hash = ?, evaluation_id = '',
                    stale_reason = 'candidate_changed', updated_at = ?, row_version = row_version + 1
                WHERE proposal_id = ? AND row_version = ?
                """,
                (target_hash, current.proposal_version + 1, _canonical(payload), candidate_hash, now, proposal_id, current.row_version),
            )
        value = self.get(proposal_id)
        if value is None:
            raise RuntimeError("proposal disappeared after revision")
        return value

    def mark_stale_for_issue(self, issue_id: str, *, current_evidence_hash: str) -> int:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE evolution_proposals
                SET status = 'stale', stale_reason = 'issue_or_capability_revision',
                    updated_at = ?, row_version = row_version + 1
                WHERE issue_id = ? AND evidence_bundle_hash <> ?
                  AND status NOT IN ('rejected', 'superseded')
                """,
                (utc_now(), issue_id, current_evidence_hash),
            )
            return cursor.rowcount

    def _find_candidate(self, issue_id: str, diagnosis_id: str, candidate_hash: str) -> Proposal | None:
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM evolution_proposals WHERE issue_id = ? AND diagnosis_id = ? AND candidate_hash = ?",
                (issue_id, diagnosis_id, candidate_hash),
            ).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: Any) -> Proposal:
        return Proposal(
            proposal_id=row["proposal_id"],
            issue_id=row["issue_id"],
            diagnosis_id=row["diagnosis_id"],
            evidence_bundle_hash=row["evidence_bundle_hash"],
            capability_id=row["capability_id"],
            capability_version_id=row["capability_version_id"],
            target_hash=row["target_hash"],
            category=row["category"],
            proposal_version=int(row["proposal_version"]),
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            candidate_hash=row["candidate_hash"],
            reviewer=row["reviewer"],
            reviewed_at=row["reviewed_at"],
            evaluation_id=row["evaluation_id"],
            stale_reason=row["stale_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            row_version=int(row["row_version"]),
        )


__all__ = ["PROPOSAL_CATEGORIES", "PROPOSAL_STATUSES", "ProposalService"]
