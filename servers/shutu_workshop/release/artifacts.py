"""Immutable, file-backed Skill artifacts for the M5 isolated release path."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..evolution.models import EvaluationSuite, Proposal, TaskCase
from ..models import utc_now


class ArtifactError(ValueError):
    """Raised when a candidate cannot be safely materialized or verified."""


_PATCH_ROOTS = {"references", "templates", "scripts", "assets"}
_ADVISORY_CATEGORIES = {
    "mcp_implementation_advisory",
    "mcp_schema",
    "agent_policy_advisory",
    "environment_action",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def artifact_tree_hash(root: str | Path) -> str:
    path = Path(root).resolve()
    if not path.is_dir():
        raise ArtifactError(f"artifact root does not exist: {path}")
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ArtifactError(f"symlinks are not allowed in artifacts: {item}")
    digest = hashlib.sha256()
    for item in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = item.relative_to(path).as_posix()
        file_hash = hashlib.sha256(item.read_bytes()).hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return digest.hexdigest()


def _safe_patch_path(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").strip("/")
    relative = PurePosixPath(normalized)
    if not normalized or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArtifactError("candidate patch path must be relative and bounded")
    if normalized != "SKILL.md" and (len(relative.parts) < 2 or relative.parts[0] not in _PATCH_ROOTS):
        raise ArtifactError("candidate patch path is outside the Skill package")
    return normalized


@dataclass(frozen=True)
class ArtifactManifest:
    proposal_id: str
    proposal_version: int
    capability_version_id: str
    target_hash: str
    baseline_root: str
    candidate_root: str
    baseline_artifact_hash: str
    candidate_artifact_hash: str
    proposal_candidate_hash: str
    patch_digest: str
    files: dict[str, str]
    manifest_hash: str = ""
    created_at: str = field(default_factory=utc_now)
    target_root: str = ""

    @property
    def artifact_hash(self) -> str:
        return self.candidate_artifact_hash

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactManifest":
        return cls(**dict(value))


class FileArtifactService:
    """Build and verify a candidate from an explicitly structured file patch."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = Path(workspace).resolve() if workspace is not None else None

    @staticmethod
    def patch_payload(proposal: Proposal | dict[str, Any]) -> list[dict[str, Any]]:
        value = proposal.to_dict() if isinstance(proposal, Proposal) else dict(proposal)
        category = str(value.get("category") or "")
        if category in _ADVISORY_CATEGORIES:
            raise ArtifactError("advisory_only_proposal_cannot_be_materialized")
        payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
        raw = payload.get("file_patch", payload.get("patches"))
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ArtifactError("proposal requires a structured file_patch list")
        patches: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ArtifactError("file_patch entries must be objects")
            path = _safe_patch_path(str(item.get("path") or ""))
            old_string = item.get("old_string")
            new_string = item.get("new_string")
            if not isinstance(old_string, str) or not isinstance(new_string, str) or not old_string:
                raise ArtifactError("file_patch requires non-empty old_string and new_string")
            patches.append(
                {
                    "path": path,
                    "old_string": old_string,
                    "new_string": new_string,
                    "replace_all": bool(item.get("replace_all", False)),
                    "old_sha256": str(item.get("old_sha256") or ""),
                    "reason": str(item.get("reason") or ""),
                }
            )
        return patches

    @staticmethod
    def _apply_patch(root: Path, patch: dict[str, Any]) -> None:
        target = root / Path(patch["path"])
        if not _within(target, root) or not target.is_file() or target.is_symlink():
            raise ArtifactError(f"candidate patch target does not exist: {patch['path']}")
        content = target.read_text(encoding="utf-8")
        old_sha = patch.get("old_sha256") or ""
        if old_sha and hashlib.sha256(content.encode("utf-8")).hexdigest() != old_sha:
            raise ArtifactError(f"candidate patch source hash mismatch: {patch['path']}")
        count = content.count(patch["old_string"])
        if count == 0 or (not patch["replace_all"] and count != 1):
            raise ArtifactError(f"candidate patch target is missing or ambiguous: {patch['path']}")
        target.write_text(
            content.replace(patch["old_string"], patch["new_string"], -1 if patch["replace_all"] else 1),
            encoding="utf-8",
        )

    @staticmethod
    def _patch_digest(patches: list[dict[str, Any]]) -> str:
        return _digest(
            [
                {
                    "path": item["path"],
                    "old_string": item["old_string"],
                    "new_string": item["new_string"],
                    "replace_all": item["replace_all"],
                    "old_sha256": item["old_sha256"],
                }
                for item in patches
            ]
        )

    def materialize(
        self,
        proposal: Proposal | dict[str, Any],
        baseline_root: str | Path,
        candidate_root: str | Path,
        *,
        isolated_root: str | Path | None = None,
    ) -> ArtifactManifest:
        proposal_value = proposal.to_dict() if isinstance(proposal, Proposal) else dict(proposal)
        if str(proposal_value.get("status") or "") not in {"accepted_for_evaluation", "evaluated"}:
            raise ArtifactError("candidate materialization requires a human-accepted proposal")
        baseline = Path(baseline_root).resolve()
        candidate = Path(candidate_root).resolve()
        allowed = Path(isolated_root or (self.workspace or candidate.parent)).resolve()
        if not baseline.is_dir() or not (baseline / "SKILL.md").is_file():
            raise ArtifactError("baseline Skill must contain SKILL.md")
        if not _within(baseline, allowed) or not _within(candidate, allowed):
            raise ArtifactError("artifact paths must stay inside the isolated root")
        if candidate.exists():
            raise ArtifactError("candidate artifact already exists")
        patches = self.patch_payload(proposal_value)
        baseline_hash = artifact_tree_hash(baseline)
        baseline_snapshot = candidate.parent / f".{candidate.name}-baseline-{baseline_hash[:12]}"
        if baseline_snapshot.exists():
            raise ArtifactError("baseline artifact snapshot already exists")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(baseline, baseline_snapshot)
        shutil.copytree(baseline, candidate)
        try:
            for patch in patches:
                self._apply_patch(candidate, patch)
            if not (candidate / "SKILL.md").is_file():
                raise ArtifactError("candidate Skill must contain SKILL.md")
            candidate_hash = artifact_tree_hash(candidate)
        except BaseException:
            shutil.rmtree(candidate, ignore_errors=True)
            shutil.rmtree(baseline_snapshot, ignore_errors=True)
            raise
        files = {
            item.relative_to(candidate).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(item for item in candidate.rglob("*") if item.is_file())
        }
        manifest_base = {
            "manifest_version": "m5-file-v1",
            "proposal_id": str(proposal_value.get("proposal_id") or ""),
            "proposal_version": int(proposal_value.get("proposal_version") or 0),
            "capability_version_id": str(proposal_value.get("capability_version_id") or ""),
            "target_hash": str(proposal_value.get("target_hash") or ""),
            "baseline_artifact_hash": baseline_hash,
            "candidate_artifact_hash": candidate_hash,
            "proposal_candidate_hash": str(proposal_value.get("candidate_hash") or ""),
            "patch_digest": self._patch_digest(patches),
            "files": files,
        }
        manifest = ArtifactManifest(
            proposal_id=manifest_base["proposal_id"],
            proposal_version=manifest_base["proposal_version"],
            capability_version_id=manifest_base["capability_version_id"],
            target_hash=manifest_base["target_hash"],
            baseline_root=str(baseline_snapshot),
            candidate_root=str(candidate),
            baseline_artifact_hash=baseline_hash,
            candidate_artifact_hash=candidate_hash,
            proposal_candidate_hash=manifest_base["proposal_candidate_hash"],
            patch_digest=manifest_base["patch_digest"],
            files=files,
            manifest_hash=_digest(manifest_base),
            target_root=str(baseline),
        )
        if self.workspace is not None:
            self.write_manifest(
                manifest,
                self.workspace / ".skill_workshop" / "m5-artifacts" / f"{manifest.proposal_id}-{manifest.proposal_version}.json",
            )
        return manifest

    @staticmethod
    def verify(manifest: ArtifactManifest | dict[str, Any]) -> bool:
        value = manifest if isinstance(manifest, ArtifactManifest) else ArtifactManifest.from_dict(manifest)
        if artifact_tree_hash(value.baseline_root) != value.baseline_artifact_hash:
            return False
        if artifact_tree_hash(value.candidate_root) != value.candidate_artifact_hash:
            return False
        current_files = {
            item.relative_to(Path(value.candidate_root).resolve()).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(item for item in Path(value.candidate_root).resolve().rglob("*") if item.is_file())
        }
        return current_files == value.files

    @staticmethod
    def write_manifest(manifest: ArtifactManifest, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target


def isolated_skill_suite() -> EvaluationSuite:
    """Two local cases: one repaired path policy and one holdout."""

    return EvaluationSuite(
        suite_id="m5-isolated-skill-v1",
        name="M5 isolated file Skill suite",
        suite_version="1",
        environment_fingerprint="isolated-file-v1",
        cases=(
            TaskCase(
                case_id="file_safe_path",
                input_ref={"task_input": {"path": "docs/guide.md", "content": "candidate"}},
                expected_behavior={"expected_output": {"valid": True, "content_hash": "candidate"}},
                baseline_output={},
                allowed_tools=("local.file",),
            ),
            TaskCase(
                case_id="file_holdout",
                input_ref={"task_input": {"path": "README.md", "content": "holdout"}},
                expected_behavior={"expected_output": {"valid": True, "content_hash": "holdout"}},
                baseline_output={},
                holdout=True,
                allowed_tools=("local.file",),
            ),
        ),
    )


__all__ = [
    "ArtifactError",
    "ArtifactManifest",
    "FileArtifactService",
    "artifact_tree_hash",
    "isolated_skill_suite",
]
