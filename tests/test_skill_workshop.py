from __future__ import annotations

import json
import multiprocessing
import sqlite3
import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from plugins.daagent.skill_workshop import SkillWorkshop
from plugins.daagent.skill_workshop.engine import (
    InvalidPatchError,
    SkillWorkshopError,
    StaleSkillError,
)


def _hold_release_lock(workspace: str, ready: object, duration: float) -> None:
    from plugins.daagent.skill_workshop.store import WorkshopStore

    store = WorkshopStore(Path(workspace) / ".skill_workshop")
    with store.release_lock(["demo-skill"]):
        ready.set()
        time.sleep(duration)


class SkillWorkshopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path.cwd() / ".test-ws" / uuid4().hex
        shutil.rmtree(self.workspace, ignore_errors=True)
        self.workspace.mkdir(parents=True)
        skill_dir = self.workspace / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo-skill\n---\n\nUse the old validation step.\n",
            encoding="utf-8",
        )
        (skill_dir / "references").mkdir()
        (skill_dir / "references" / "notes.md").write_text("reference\n", encoding="utf-8")
        self.workshop = SkillWorkshop(self.workspace)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_snapshot_is_deterministic_and_lists_skill(self) -> None:
        self.assertEqual(self.workshop.list_skills(), ["demo-skill"])
        first = self.workshop.snapshot_skill("demo-skill")
        second = self.workshop.snapshot_skill("demo-skill")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertIn("SKILL.md", first.files)

    def test_proposal_test_does_not_mutate_and_apply_creates_version(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Clarify validation guidance",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "Use the old validation step.",
                    "new_string": "Use the explicit validation step.",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        report = self.workshop.test_proposal(proposal.id)
        self.assertTrue(report["passed"])
        self.assertIn("old validation", (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8"))

        version = self.workshop.apply_proposal(proposal.id)
        current = (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("explicit validation", current)
        self.assertEqual(self.workshop.get_proposal(proposal.id).status, "applied")
        self.assertGreaterEqual(len(self.workshop.list_versions("demo-skill")), 2)
        self.assertEqual(self.workshop.list_versions("demo-skill")[-1].id, version.id)
        operation = self.workshop.store.list_release_operations()[-1]
        self.assertEqual(operation.kind, "existing")
        self.assertEqual(operation.status, "completed")

    def test_stale_proposal_is_rejected(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Change guidance",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "new validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        path = self.workspace / "skills" / "demo-skill" / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")
        report = self.workshop.test_proposal(proposal.id)
        self.assertFalse(report["passed"])
        self.assertIn("StaleSkillError", report["errors"][0])
        with self.assertRaises(StaleSkillError):
            self.workshop.apply_proposal(proposal.id)

    def test_apply_as_new_skill_preserves_source(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Fork guidance",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "fork validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])
        version = self.workshop.apply_proposal_as_new_skill(proposal.id, "forked-skill")
        source = (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
        fork = (self.workspace / "skills" / "forked-skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("old validation", source)
        self.assertIn("fork validation", fork)
        self.assertIn("name: forked-skill", fork)
        self.assertEqual(version.source, "fork")
        operation = self.workshop.store.list_release_operations()[-1]
        self.assertEqual(operation.kind, "fork")
        self.assertEqual(operation.status, "completed")

    def test_patch_path_is_bounded(self) -> None:
        with self.assertRaises(InvalidPatchError):
            self.workshop.create_proposal(
                "demo-skill",
                reason="Unsafe",
                changes=[
                    {
                        "path": "../../outside.txt",
                        "old_string": "x",
                        "new_string": "y",
                    }
                ],
            )

    def test_rollback_restores_previous_snapshot(self) -> None:
        baseline = self.workshop.snapshot_skill("demo-skill")
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Change guidance",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "new validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])
        applied = self.workshop.apply_proposal(proposal.id)
        versions = self.workshop.list_versions("demo-skill")
        baseline_version = next(version for version in versions if version.package_hash == baseline.content_hash)
        rollback_version = self.workshop.rollback("demo-skill", baseline_version.id)
        current = (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("old validation", current)
        self.assertEqual(rollback_version.source, "rollback")
        self.assertNotEqual(rollback_version.id, applied.id)
        operation = self.workshop.store.list_release_operations()[-1]
        self.assertEqual(operation.kind, "rollback")
        self.assertEqual(operation.status, "completed")

    def test_review_change_invalidates_test_fingerprint(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Review two changes",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "reviewed validation",
                },
                {
                    "path": "references/notes.md",
                    "old_string": "reference",
                    "new_string": "reviewed reference",
                },
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])
        tested = self.workshop.get_proposal(proposal.id)
        self.assertTrue(tested.test_fingerprint)
        changed_patch_id = tested.changes[1].id

        self.workshop.accept_patch(proposal.id, changed_patch_id, accepted=False)
        invalidated = self.workshop.get_proposal(proposal.id)
        self.assertEqual(invalidated.status, "pending_review")
        self.assertEqual(invalidated.test_result, {})
        self.assertEqual(invalidated.test_fingerprint, "")
        with self.assertRaises(SkillWorkshopError):
            self.workshop.apply_proposal(proposal.id)

    def test_candidate_fingerprint_rejects_tampered_test_result(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Fingerprint gate",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "fingerprinted validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])
        tampered = self.workshop.get_proposal(proposal.id)
        tampered.test_result["candidate_hash"] = "0" * 64
        self.workshop.store.save_proposal(tampered)
        with self.assertRaises(SkillWorkshopError):
            self.workshop.apply_proposal(proposal.id)

    def test_crash_after_file_swap_is_recovered_on_startup(self) -> None:
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Crash recovery",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "recovered validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])

        with patch.object(
            self.workshop,
            "_commit_version",
            side_effect=KeyboardInterrupt("injected crash"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.workshop.apply_proposal(proposal.id)

        pending = [
            operation
            for operation in self.workshop.store.list_release_operations()
            if operation.status not in {"completed", "failed"}
        ]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].status, "files_swapped")

        recovered = SkillWorkshop(self.workspace)
        current = (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("recovered validation", current)
        self.assertEqual(recovered.store.list_release_operations()[-1].status, "completed")
        self.assertEqual(recovered.get_proposal(proposal.id).status, "applied")
        self.assertTrue(recovered.list_versions("demo-skill")[-1].snapshot_path)

    def test_concurrent_apply_serializes_and_rejects_stale_head(self) -> None:
        left = SkillWorkshop(self.workspace)
        right = SkillWorkshop(self.workspace)
        left_proposal = left.create_proposal(
            "demo-skill",
            reason="Left concurrent change",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "left validation",
                }
            ],
        )
        right_proposal = right.create_proposal(
            "demo-skill",
            reason="Right concurrent change",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "right validation",
                }
            ],
        )
        left.accept_all(left_proposal.id)
        right.accept_all(right_proposal.id)
        self.assertTrue(left.test_proposal(left_proposal.id)["passed"])
        self.assertTrue(right.test_proposal(right_proposal.id)["passed"])
        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, object]] = []

        def apply(workshop: SkillWorkshop, proposal_id: str) -> None:
            barrier.wait()
            try:
                outcomes.append(("ok", workshop.apply_proposal(proposal_id)))
            except Exception as exc:  # noqa: BLE001 - collect both race outcomes
                outcomes.append(("error", exc))

        threads = [
            threading.Thread(target=apply, args=(left, left_proposal.id)),
            threading.Thread(target=apply, args=(right, right_proposal.id)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([kind for kind, _ in outcomes].count("ok"), 1)
        errors = [value for kind, value in outcomes if kind == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], StaleSkillError)
        current = (self.workspace / "skills" / "demo-skill" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertTrue("left validation" in current or "right validation" in current)

    def test_rollback_rejects_manual_head_drift(self) -> None:
        baseline = self.workshop.snapshot_skill("demo-skill")
        proposal = self.workshop.create_proposal(
            "demo-skill",
            reason="Create head",
            changes=[
                {
                    "path": "SKILL.md",
                    "old_string": "old validation",
                    "new_string": "head validation",
                }
            ],
        )
        self.workshop.accept_all(proposal.id)
        self.assertTrue(self.workshop.test_proposal(proposal.id)["passed"])
        self.workshop.apply_proposal(proposal.id)
        path = self.workspace / "skills" / "demo-skill" / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8") + "manual drift\n", encoding="utf-8")
        baseline_version = next(
            version
            for version in self.workshop.list_versions("demo-skill")
            if version.package_hash == baseline.content_hash
        )
        with self.assertRaises(StaleSkillError):
            self.workshop.rollback("demo-skill", baseline_version.id)

    def test_legacy_sqlite_state_is_migrated_additively(self) -> None:
        legacy_root = self.workspace / "legacy-store"
        legacy_root.mkdir()
        payload = {
            "skill_name": "demo-skill",
            "reason": "legacy",
            "base_hash": "legacy-hash",
            "changes": [],
            "id": "legacy-proposal",
            "status": "pending_review",
            "risk_level": "low",
            "test_result": {},
            "applied_version_id": "",
            "apply_mode": "existing",
            "target_skill_name": "",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        connection = sqlite3.connect(legacy_root / "state.db")
        connection.executescript(
            """
            CREATE TABLE proposals (
                id TEXT PRIMARY KEY,
                skill_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO proposals VALUES (?, ?, ?, ?, ?, ?)",
            (
                "legacy-proposal",
                "demo-skill",
                "pending_review",
                payload["created_at"],
                payload["updated_at"],
                json.dumps(payload),
            ),
        )
        connection.commit()
        connection.close()

        from plugins.daagent.skill_workshop.store import WorkshopStore

        store = WorkshopStore(legacy_root)
        loaded = store.get_proposal("legacy-proposal")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.test_fingerprint, "")
        check_connection = sqlite3.connect(legacy_root / "state.db")
        columns = check_connection.execute("PRAGMA table_info(proposals)").fetchall()
        check_connection.close()
        self.assertIn("row_version", {row[1] for row in columns})
        self.assertEqual(len(store.list_release_operations()), 0)

    def test_release_lock_serializes_across_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        process = context.Process(
            target=_hold_release_lock,
            args=(str(self.workspace), ready, 0.4),
        )
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            started = time.monotonic()
            with self.workshop.store.release_lock(["demo-skill"]):
                elapsed = time.monotonic() - started
            process.join(10)
            self.assertGreaterEqual(elapsed, 0.25)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)


if __name__ == "__main__":
    unittest.main()
