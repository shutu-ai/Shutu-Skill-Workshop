"""Core local implementation of the DAAgent Skill Workshop lifecycle."""

from __future__ import annotations

import hashlib
import re
import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from uuid import uuid4

from .models import (
    EvolutionIssue,
    Patch,
    Proposal,
    SkillSnapshot,
    Version,
    utc_now,
)
from .store import WorkshopStore


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PATCH_ROOTS = {"references", "templates", "scripts", "assets"}


class SkillWorkshopError(RuntimeError):
    """Base error for expected workshop validation and lifecycle failures."""


class StaleSkillError(SkillWorkshopError):
    """Raised when the skill changed after a proposal was generated."""


class InvalidPatchError(SkillWorkshopError):
    """Raised when a patch violates the skill package boundary."""


class SkillWorkshop:
    """Manage local skill packages without touching the DAAgent install tree."""

    def __init__(self, workspace: str | Path, skills_dir: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.skills_dir = (
            Path(skills_dir).expanduser().resolve()
            if skills_dir is not None
            else self.workspace / "skills"
        )
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.store = WorkshopStore(self.workspace / ".skill_workshop")

    @contextmanager
    def _staging_directory(self) -> Iterator[Path]:
        path = self.store.staging_root / f"work-{uuid4().hex}"
        path.mkdir()
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _validate_name(name: str) -> str:
        value = str(name or "").strip()
        if not _SAFE_NAME.fullmatch(value):
            raise SkillWorkshopError(f"Invalid skill name: {name!r}")
        return value

    def _skill_dir(self, skill_name: str) -> Path:
        name = self._validate_name(skill_name)
        root = self.skills_dir.resolve()
        path = (root / name).resolve()
        if not self._within(path, root):
            raise SkillWorkshopError("Skill path escapes the workspace skill directory")
        if not path.is_dir():
            raise SkillWorkshopError(f"Skill directory does not exist: {name}")
        if not (path / "SKILL.md").is_file():
            raise SkillWorkshopError(f"Skill is missing SKILL.md: {name}")
        return path

    @staticmethod
    def _validate_patch_path(file_path: str) -> str:
        normalized = str(file_path or "").replace("\\", "/").strip("/")
        if not normalized or "//" in normalized:
            raise InvalidPatchError("Invalid skill patch path")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise InvalidPatchError("Skill patch path must stay inside the skill package")
        if normalized == "SKILL.md":
            return normalized
        if len(relative.parts) < 2 or relative.parts[0] not in _PATCH_ROOTS:
            raise InvalidPatchError(
                "Only SKILL.md, references/, templates/, scripts/, and assets/ may change"
            )
        return normalized

    @staticmethod
    def _assert_no_symlinks(root: Path) -> None:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise SkillWorkshopError(f"Symlinks are not allowed in skill packages: {path}")

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _snapshot_dir(self, skill_name: str, skill_dir: Path) -> SkillSnapshot:
        self._assert_no_symlinks(skill_dir)
        digest = hashlib.sha256()
        files: dict[str, str] = {}
        for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(skill_dir).as_posix()
            file_hash = self._hash_file(path)
            files[relative] = file_hash
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(bytes.fromhex(file_hash))
        return SkillSnapshot(
            skill_name=skill_name,
            skill_dir=str(skill_dir),
            content_hash=digest.hexdigest(),
            files=files,
        )

    def snapshot_skill(self, skill_name: str) -> SkillSnapshot:
        """Capture a deterministic content fingerprint for one local skill."""

        skill_dir = self._skill_dir(skill_name)
        return self._snapshot_dir(self._validate_name(skill_name), skill_dir)

    def list_skills(self) -> list[str]:
        return sorted(
            path.name
            for path in self.skills_dir.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        )

    def record_issue(
        self,
        skill_name: str,
        *,
        category: str,
        severity: str,
        title: str,
        summary: str = "",
        evidence_count: int = 0,
    ) -> EvolutionIssue:
        self._skill_dir(skill_name)
        issue = EvolutionIssue(
            skill_name=self._validate_name(skill_name),
            category=category,
            severity=severity,
            title=title,
            summary=summary,
            evidence_count=max(0, int(evidence_count)),
        )
        return self.store.save_issue(issue)

    @staticmethod
    def _coerce_patch(value: Patch | dict[str, Any]) -> Patch:
        if isinstance(value, Patch):
            return Patch(
                path=value.path,
                old_string=value.old_string,
                new_string=value.new_string,
                replace_all=value.replace_all,
                reason=value.reason,
                id=value.id,
                status=value.status,
            )
        return Patch(
            path=str(value.get("path", "")),
            old_string=str(value.get("old_string", "")),
            new_string=str(value.get("new_string", "")),
            replace_all=bool(value.get("replace_all", False)),
            reason=str(value.get("reason", "")),
        )

    @staticmethod
    def _apply_text(content: str, patch: Patch) -> str:
        if not patch.old_string:
            raise InvalidPatchError(f"Patch {patch.id} has an empty old_string")
        count = content.count(patch.old_string)
        if count == 0:
            raise InvalidPatchError(
                f"Patch target text was not found in {patch.path}: {patch.id}"
            )
        if not patch.replace_all and count != 1:
            raise InvalidPatchError(
                f"Patch target is ambiguous in {patch.path}; use replace_all explicitly"
            )
        return content.replace(patch.old_string, patch.new_string, -1 if patch.replace_all else 1)

    def create_proposal(
        self,
        skill_name: str,
        *,
        reason: str,
        changes: Iterable[Patch | dict[str, Any]],
        risk_level: str = "low",
    ) -> Proposal:
        """Create a reviewable proposal from exact text patches.

        This is intentionally provider-neutral: an LLM or a human can generate
        the structured patches, while the workshop owns validation and apply.
        """

        name = self._validate_name(skill_name)
        skill_dir = self._skill_dir(name)
        base = self._snapshot_dir(name, skill_dir)
        contents: dict[str, str] = {}
        normalized: list[Patch] = []
        for raw_change in changes:
            patch = self._coerce_patch(raw_change)
            patch.path = self._validate_patch_path(patch.path)
            target = skill_dir / Path(patch.path)
            if not target.is_file():
                raise InvalidPatchError(f"Patch target does not exist: {patch.path}")
            if patch.path not in contents:
                try:
                    contents[patch.path] = target.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise InvalidPatchError(f"Patch target is not UTF-8 text: {patch.path}") from exc
            contents[patch.path] = self._apply_text(contents[patch.path], patch)
            normalized.append(patch)
        if not normalized:
            raise InvalidPatchError("A proposal must contain at least one patch")
        proposal = Proposal(
            skill_name=name,
            reason=str(reason or "").strip(),
            base_hash=base.content_hash,
            changes=normalized,
            risk_level=str(risk_level or "low"),
        )
        return self.store.save_proposal(proposal)

    def _require_proposal(self, proposal_id: str) -> Proposal:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            raise SkillWorkshopError(f"Proposal does not exist: {proposal_id}")
        return proposal

    def _save_proposal(self, proposal: Proposal) -> Proposal:
        proposal.updated_at = utc_now()
        return self.store.save_proposal(proposal)

    def accept_patch(self, proposal_id: str, patch_id: str, *, accepted: bool) -> Proposal:
        proposal = self._require_proposal(proposal_id)
        for patch in proposal.changes:
            if patch.id == patch_id:
                patch.status = "accepted" if accepted else "rejected"
                return self._save_proposal(proposal)
        raise SkillWorkshopError(f"Patch does not exist in proposal: {patch_id}")

    def accept_all(self, proposal_id: str) -> Proposal:
        proposal = self._require_proposal(proposal_id)
        for patch in proposal.changes:
            patch.status = "accepted"
        return self._save_proposal(proposal)

    def reject_all(self, proposal_id: str) -> Proposal:
        proposal = self._require_proposal(proposal_id)
        for patch in proposal.changes:
            patch.status = "rejected"
        proposal.status = "rejected"
        return self._save_proposal(proposal)

    @staticmethod
    def _accepted_changes(proposal: Proposal) -> list[Patch]:
        return [patch for patch in proposal.changes if patch.status == "accepted"]

    @staticmethod
    def _validate_skill_tree(skill_dir: Path) -> None:
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            raise SkillWorkshopError("Candidate skill must contain SKILL.md")
        SkillWorkshop._assert_no_symlinks(skill_dir)

    def _apply_changes_to_directory(self, skill_dir: Path, changes: list[Patch]) -> None:
        for patch in changes:
            relative = self._validate_patch_path(patch.path)
            target = skill_dir / Path(relative)
            if not self._within(target, skill_dir) or not target.is_file():
                raise InvalidPatchError(f"Patch target does not exist: {relative}")
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise InvalidPatchError(f"Patch target is not UTF-8 text: {relative}") from exc
            target.write_text(self._apply_text(content, patch), encoding="utf-8")

    def test_proposal(self, proposal_id: str) -> dict[str, Any]:
        """Apply accepted patches to a temporary copy and return a test report."""

        proposal = self._require_proposal(proposal_id)
        report: dict[str, Any] = {
            "passed": False,
            "proposal_id": proposal.id,
            "errors": [],
        }
        try:
            skill_dir = self._skill_dir(proposal.skill_name)
            current = self._snapshot_dir(proposal.skill_name, skill_dir)
            if current.content_hash != proposal.base_hash:
                raise StaleSkillError(
                    "The skill changed after this proposal was created; regenerate it"
                )
            changes = self._accepted_changes(proposal)
            if not changes:
                raise SkillWorkshopError("Accept at least one patch before testing")
            with self._staging_directory() as temporary:
                candidate = temporary / "candidate"
                shutil.copytree(skill_dir, candidate)
                self._apply_changes_to_directory(candidate, changes)
                self._validate_skill_tree(candidate)
                candidate_snapshot = self._snapshot_dir(proposal.skill_name, candidate)
                report.update(
                    {
                        "passed": True,
                        "candidate_hash": candidate_snapshot.content_hash,
                        "accepted_patch_ids": [patch.id for patch in changes],
                    }
                )
        except Exception as exc:  # noqa: BLE001 - persisted as a user-facing report
            report["errors"] = [f"{type(exc).__name__}: {exc}"]
        proposal.test_result = report
        proposal.status = "test_passed" if report["passed"] else "test_failed"
        self._save_proposal(proposal)
        return report

    def _ensure_baseline_version(self, skill_name: str, snapshot: SkillSnapshot) -> Version:
        versions = self.store.list_versions(skill_name)
        if versions and versions[-1].package_hash == snapshot.content_hash:
            return versions[-1]
        version = Version(
            skill_name=skill_name,
            package_hash=snapshot.content_hash,
            snapshot_path="",
            source="baseline",
            message="Workspace baseline",
            parent_version_id=versions[-1].id if versions else "",
        )
        version_dir = self.store.versions_root / skill_name / version.id
        version_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot.skill_dir, version_dir)
        version.snapshot_path = str(version_dir)
        return self.store.save_version(version)

    def _commit_version(
        self,
        skill_name: str,
        skill_dir: Path,
        *,
        source: str,
        message: str,
        parent_version_id: str,
    ) -> Version:
        snapshot = self._snapshot_dir(skill_name, skill_dir)
        version = Version(
            skill_name=skill_name,
            package_hash=snapshot.content_hash,
            snapshot_path="",
            source=source,
            message=message,
            parent_version_id=parent_version_id,
        )
        version_dir = self.store.versions_root / skill_name / version.id
        version_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir, version_dir)
        version.snapshot_path = str(version_dir)
        return self.store.save_version(version)

    @staticmethod
    def _rewrite_skill_name(skill_md: Path, skill_name: str) -> None:
        content = skill_md.read_text(encoding="utf-8")
        if content.startswith("---\n"):
            closing = content.find("\n---", 4)
            if closing >= 0:
                frontmatter = content[4:closing].splitlines()
                replaced = False
                for index, line in enumerate(frontmatter):
                    if line.startswith("name:"):
                        frontmatter[index] = f"name: {skill_name}"
                        replaced = True
                if not replaced:
                    frontmatter.insert(0, f"name: {skill_name}")
                body = content[closing + 4 :]
                skill_md.write_text("---\n" + "\n".join(frontmatter) + "\n---" + body, encoding="utf-8")
                return
        skill_md.write_text(f"---\nname: {skill_name}\n---\n\n{content}", encoding="utf-8")

    def apply_proposal(self, proposal_id: str) -> Version:
        """Atomically apply a tested proposal to the existing skill."""

        proposal = self._require_proposal(proposal_id)
        skill_dir = self._skill_dir(proposal.skill_name)
        current = self._snapshot_dir(proposal.skill_name, skill_dir)
        if current.content_hash != proposal.base_hash:
            raise StaleSkillError("The skill changed after testing; test the proposal again")
        if not proposal.test_result.get("passed") or proposal.status != "test_passed":
            raise SkillWorkshopError("A proposal must pass test_proposal before apply")
        changes = self._accepted_changes(proposal)
        baseline = self._ensure_baseline_version(proposal.skill_name, current)

        with self._staging_directory() as staging:
            candidate = staging / "candidate"
            previous = staging / "previous"
            shutil.copytree(skill_dir, candidate)
            self._apply_changes_to_directory(candidate, changes)
            self._validate_skill_tree(candidate)
            skill_dir.replace(previous)
            try:
                candidate.replace(skill_dir)
                version = self._commit_version(
                    proposal.skill_name,
                    skill_dir,
                    source="evolution",
                    message=proposal.reason or "Apply accepted skill evolution changes",
                    parent_version_id=baseline.id,
                )
            except Exception:
                if skill_dir.exists():
                    failed = staging / "failed_candidate"
                    skill_dir.replace(failed)
                previous.replace(skill_dir)
                raise

        proposal.status = "applied"
        proposal.applied_version_id = version.id
        proposal.apply_mode = "existing"
        proposal.target_skill_name = proposal.skill_name
        self._save_proposal(proposal)
        return version

    def apply_proposal_as_new_skill(self, proposal_id: str, new_skill_name: str) -> Version:
        """Create a new skill from a tested proposal without replacing its source."""

        proposal = self._require_proposal(proposal_id)
        if not proposal.test_result.get("passed") or proposal.status != "test_passed":
            raise SkillWorkshopError("A proposal must pass test_proposal before apply")
        source_dir = self._skill_dir(proposal.skill_name)
        new_name = self._validate_name(new_skill_name)
        target_dir = self.skills_dir / new_name
        if target_dir.exists():
            raise SkillWorkshopError(f"Target skill already exists: {new_name}")
        current = self._snapshot_dir(proposal.skill_name, source_dir)
        if current.content_hash != proposal.base_hash:
            raise StaleSkillError("The source skill changed after testing; test again")
        changes = self._accepted_changes(proposal)
        with self._staging_directory() as temporary:
            candidate = temporary / "candidate"
            shutil.copytree(source_dir, candidate)
            self._apply_changes_to_directory(candidate, changes)
            self._rewrite_skill_name(candidate / "SKILL.md", new_name)
            self._validate_skill_tree(candidate)
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            candidate.replace(target_dir)
        parent_versions = self.store.list_versions(proposal.skill_name)
        version = self._commit_version(
            new_name,
            target_dir,
            source="fork",
            message=f"Create {new_name} from {proposal.skill_name}",
            parent_version_id=parent_versions[-1].id if parent_versions else "",
        )
        proposal.status = "applied"
        proposal.applied_version_id = version.id
        proposal.apply_mode = "fork"
        proposal.target_skill_name = new_name
        self._save_proposal(proposal)
        return version

    def rollback(self, skill_name: str, version_id: str) -> Version:
        """Restore a recorded version and create a new rollback version."""

        name = self._validate_name(skill_name)
        target = self._skill_dir(name)
        selected = self.store.get_version(version_id)
        if selected is None or selected.skill_name != name:
            raise SkillWorkshopError(f"Version does not belong to skill: {version_id}")
        snapshot_dir = Path(selected.snapshot_path).resolve()
        if not snapshot_dir.is_dir() or not self._within(snapshot_dir, self.store.versions_root):
            raise SkillWorkshopError("Version snapshot is missing or outside the workshop store")
        versions = self.store.list_versions(name)
        if not versions:
            raise SkillWorkshopError("No current version exists for rollback")
        with self._staging_directory() as staging:
            candidate = staging / "candidate"
            previous = staging / "previous"
            shutil.copytree(snapshot_dir, candidate)
            self._validate_skill_tree(candidate)
            target.replace(previous)
            try:
                candidate.replace(target)
                version = self._commit_version(
                    name,
                    target,
                    source="rollback",
                    message=f"Rollback to {version_id}",
                    parent_version_id=versions[-1].id,
                )
            except Exception:
                if target.exists():
                    failed = staging / "failed_candidate"
                    target.replace(failed)
                previous.replace(target)
                raise
        return version

    def get_proposal(self, proposal_id: str) -> Proposal:
        return self._require_proposal(proposal_id)

    def list_proposals(self, skill_name: str | None = None) -> list[Proposal]:
        return self.store.list_proposals(skill_name)

    def list_issues(self, skill_name: str | None = None) -> list[EvolutionIssue]:
        return self.store.list_issues(skill_name)

    def list_versions(self, skill_name: str) -> list[Version]:
        self._validate_name(skill_name)
        return self.store.list_versions(skill_name)
