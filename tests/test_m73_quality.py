from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    JointAttribution,
    QualityCase,
    QualityService,
    SkillRegistry,
    SkillRoutingEvaluator,
    SkillSelectionLabel,
    ThreeArmQualityRunner,
    ThirdPartyMcpManager,
)


class M73QualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _skill(self, path: Path, description: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(
            f"---\nname: shared-name\ndescription: {description}\n---\n\n# Local\n",
            encoding="utf-8",
        )
        (path / "references").mkdir(exist_ok=True)
        (path / "references" / "guide.txt").write_text("bounded reference", encoding="utf-8")

    def _arm(self, path: Path, label: str, *, skill: bool) -> None:
        (path / "scripts").mkdir(parents=True)
        if skill:
            self._skill(path, label)
        (path / "scripts" / "validator.py").write_text(
            "import json, sys\n"
            f"print(json.dumps({{'answer': {label!r} if json.load(sys.stdin).get('value') == 'target' else 'normal'}}))\n",
            encoding="utf-8",
        )

    def test_skill_registry_keeps_scope_identity_and_does_not_claim_host_invocation(self) -> None:
        global_skill = self.root / "global" / "shared"
        project_skill = self.root / "project" / "shared"
        self._skill(global_skill, "global")
        self._skill(project_skill, "project")
        values = SkillRegistry(self.workspace).discover([global_skill, project_skill], scope="project")
        self.assertEqual(len(values), 2)
        self.assertNotEqual(values[0].capability_id, values[1].capability_id)
        self.assertTrue(all(item.statuses["available"] for item in values))
        self.assertTrue(all(item.statuses["invoked"] is None for item in values))
        with SkillRegistry(self.workspace).db.connection() as connection:
            row = connection.execute("SELECT metadata_json FROM capability_versions").fetchone()
        self.assertNotIn("bounded reference", row[0])

    def test_non_utf8_skill_is_partial_not_host_observed(self) -> None:
        skill = self.root / "bad"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(b"---\nname: bad\n---\n\xff")
        value = SkillRegistry(self.workspace).discover([skill])[0]
        self.assertIn("skill_md_not_utf8", value.errors)
        self.assertEqual(value.evidence_tier, "ARTIFACT_ACCESS_OBSERVED")
        self.assertIsNone(value.statuses["invoked"])

    def test_routing_excludes_forced_and_keeps_unknown_natural_metrics(self) -> None:
        labels = [
            SkillSelectionLabel("positive", "a", "skill:a", "yes"),
            SkillSelectionLabel("negative", "b", "skill:a", "no"),
            SkillSelectionLabel("forced", "c", "skill:a", "yes", selection_mode="FORCED", observed_invocation=True, observation_evidence_tier="EXPERIMENT_CONDITION"),
        ]
        report = SkillRoutingEvaluator().evaluate(labels)
        self.assertEqual(report.summary["forced_excluded"], 1)
        self.assertEqual(next(item for item in report.metrics if item.name == "precision").value, "N/A")
        self.assertIn("skill_actual_invocation_observed", report.missing_fields)

    def test_three_arm_runner_uses_external_oracle_and_frozen_hashes(self) -> None:
        no_skill = self.root / "no-skill"
        baseline = self.root / "baseline"
        candidate = self.root / "candidate"
        self._arm(no_skill, "none", skill=False)
        self._arm(baseline, "baseline", skill=True)
        self._arm(candidate, "candidate", skill=True)
        cases = (
            QualityCase("target", {"value": "target"}, "target", target_skill_id="skill:shared"),
            QualityCase("normal-regression", {"value": "normal"}, "normal-regression", target_skill_id="skill:shared"),
            QualityCase("holdout", {"value": "target"}, "holdout", holdout=True, target_skill_id="skill:shared"),
        )
        seen_inputs: list[dict[str, object]] = []

        def oracle(case: QualityCase, output: object) -> dict[str, object]:
            seen_inputs.append(case.runner_input())
            expected = "candidate" if case.category == "target" else "normal"
            return {"passed": isinstance(output, dict) and output.get("answer") == expected}

        report = ThreeArmQualityRunner().run(cases, {"no_skill": no_skill, "baseline": baseline, "candidate": candidate}, oracle, persist=QualityService(self.workspace))
        self.assertEqual(report.summary["case_categories"], {"holdout": 1, "normal-regression": 1, "target": 1})
        self.assertEqual(report.summary["verdict"], "IMPROVED")
        self.assertTrue(all("expected" not in value for value in seen_inputs))
        summary = QualityService(self.workspace).summary(report_kind="skill_three_arm")
        self.assertEqual(summary["status"], "PASS")

    def test_three_arm_rejects_no_skill_leak_and_same_candidate_hash(self) -> None:
        no_skill = self.root / "no-skill"
        baseline = self.root / "baseline"
        candidate = self.root / "candidate"
        self._arm(no_skill, "none", skill=True)
        self._arm(baseline, "baseline", skill=True)
        self._arm(candidate, "baseline", skill=True)
        cases = (
            QualityCase("target", {"value": "target"}, "target"),
            QualityCase("normal-regression", {"value": "normal"}, "normal-regression"),
            QualityCase("holdout", {"value": "target"}, "holdout", holdout=True),
        )
        with self.assertRaises(ValueError):
            ThreeArmQualityRunner().run(cases, {"no_skill": no_skill, "baseline": baseline, "candidate": candidate}, lambda _case, _output: True)

    def test_joint_attribution_rejects_bare_cross_session_request_id(self) -> None:
        finding = JointAttribution().classify(
            target_kind="trace",
            target_id="trace-1",
            classification="mcp_error",
            primary_owner="skill",
            contributing_owners=("skill", "mcp_tool"),
            evidence_refs=("event-1",),
            correlation_quality="weak",
            verified_bridge=False,
        )
        self.assertEqual(finding.primary_owner, "unknown")
        self.assertEqual(finding.classification, "UNVERIFIED_HYPOTHESIS")

    def test_third_party_manifest_is_explicit_reversible_and_real_stdio_probe(self) -> None:
        manager = ThirdPartyMcpManager(self.workspace)
        command = [sys.executable, str(Path(__file__).parent / "fixtures" / "mcp_test_server.py")]
        registration = manager.enable("owned-fixture", command)
        self.assertEqual(registration.status, "enabled")
        self.assertEqual(manager.verify("owned-fixture")["status"], "PASS")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "safe"}}},
        ]
        probe = manager.run_probe("owned-fixture", messages, workspace=self.workspace, timeout=5)
        self.assertEqual(probe["status"], "PASS", probe)
        self.assertEqual(probe["report"]["failed_observations"], 0)
        self.assertEqual(manager.disable("owned-fixture").status, "disabled")
        self.assertFalse(manager.verify("owned-fixture")["active"])
        with self.assertRaises(ValueError):
            manager.enable("shutu-workshop", command)
        with self.assertRaises(ValueError):
            manager.enable("secret-fixture", command, env={"API_TOKEN": "hidden"})


if __name__ == "__main__":
    unittest.main()
