from __future__ import annotations

import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from plugins.daagent.skill_workshop import SkillWorkshop
from plugins.daagent.skill_workshop.engine import InvalidPatchError, StaleSkillError


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


if __name__ == "__main__":
    unittest.main()
