"""Domain records for the local Skill Workshop replica."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass
class SkillSnapshot:
    skill_name: str
    skill_dir: str
    content_hash: str
    files: dict[str, str]
    captured_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvolutionIssue:
    skill_name: str
    category: str
    severity: str
    title: str
    summary: str = ""
    id: str = field(default_factory=lambda: new_id("issue"))
    status: str = "open"
    evidence_count: int = 0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Patch:
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False
    reason: str = ""
    id: str = field(default_factory=lambda: new_id("patch"))
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    skill_name: str
    reason: str
    base_hash: str
    changes: list[Patch]
    id: str = field(default_factory=lambda: new_id("proposal"))
    status: str = "pending_review"
    risk_level: str = "low"
    test_result: dict[str, Any] = field(default_factory=dict)
    applied_version_id: str = ""
    apply_mode: str = "existing"
    target_skill_name: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    test_fingerprint: str = ""
    accepted_change_digest: str = ""
    row_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Proposal":
        changes = [Patch(**item) for item in payload.get("changes", [])]
        values = dict(payload)
        values["changes"] = changes
        return cls(**values)


@dataclass
class ReleaseOperation:
    """Durable cross-filesystem/SQLite release journal entry."""

    kind: str
    skill_name: str
    target_skill_name: str
    base_hash: str
    source: str
    message: str
    parent_version_id: str
    operation_id: str = field(default_factory=lambda: new_id("release"))
    proposal_id: str = ""
    target_version_id: str = ""
    candidate_hash: str = ""
    staging_ref: str = ""
    previous_ref: str = ""
    status: str = "prepared"
    error: str = ""
    attempt_count: int = 0
    last_reconciled_at: str = ""
    version_created_at: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReleaseOperation":
        return cls(**payload)


@dataclass
class Version:
    skill_name: str
    package_hash: str
    snapshot_path: str
    source: str = "evolution"
    message: str = ""
    parent_version_id: str = ""
    id: str = field(default_factory=lambda: new_id("version"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Version":
        return cls(**payload)
