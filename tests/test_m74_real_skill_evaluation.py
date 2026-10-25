from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    CodexRealTaskRunner,
    M1Database,
    QualityCase,
    RealCodexThreeArmRunner,
    SkillRoutingEvaluator,
    SkillSelectionLabel,
    ThreeArmQualityRunner,
)
from shutu_workshop.observers.mcp.common import McpTraceRecorder
from shutu_workshop.ingestion import TraceIngestor


class M74EvidenceIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _arm(self, path: Path, label: str, *, skill: bool = True, mutate: bool = False) -> None:
        (path / "scripts").mkdir(parents=True)
        if skill:
            (path / "SKILL.md").write_text(f"---\nname: {label}\n---\n", encoding="utf-8")
        extra = "\nopen('runtime-change.txt', 'w').write('changed')" if mutate else ""
        (path / "scripts" / "validator.py").write_text(
            "import json, sys\n"
            f"{extra}\n"
            f"print(json.dumps({{'answer': {label!r}}}))\n",
            encoding="utf-8",
        )

    def _cases(self) -> tuple[QualityCase, ...]:
        return (
            QualityCase("target", {"value": "target"}, "target", target_skill_id="skill:target"),
            QualityCase("normal", {"value": "normal"}, "normal-regression", target_skill_id="skill:target"),
            QualityCase("holdout", {"value": "holdout"}, "holdout", holdout=True, target_skill_id="skill:target"),
        )

    def test_native_routing_requires_verifiable_scoped_host_event(self) -> None:
        label = SkillSelectionLabel(
            "case-1", "input", "skill:target", "yes", observed_invocation=True,
            observation_evidence_tier="HOST_OBSERVED", host_event_ref="event-1",
        )
        report = SkillRoutingEvaluator().evaluate([label], host_events={})
        self.assertEqual(next(metric for metric in report.metrics if metric.name == "precision").value, "N/A")
        self.assertEqual(report.summary["unknown_reasons"]["case-1"], "host_event_index_missing")

        mismatch = SkillSelectionLabel(
            "case-2", "input", "skill:target", "yes", observed_invocation=True,
            observation_evidence_tier="HOST_OBSERVED", host_event_ref="event-2",
            source_scope={"version_id": "v2"},
        )
        report = SkillRoutingEvaluator().evaluate(
            [mismatch],
            host_events={"event-2": {"event_id": "event-2", "type": "skill.invoked", "capability_id": "skill:target", "version_id": "v1"}},
        )
        self.assertEqual(report.summary["selection_truth"], "NOT_OBSERVABLE")

        trusted = SkillSelectionLabel(
            "case-3", "input", "skill:target", "yes", observed_invocation=True,
            observation_evidence_tier="HOST_OBSERVED", host_event_ref="event-3",
            source_scope={"scope": "project", "version_id": "v3"},
        )
        report = SkillRoutingEvaluator().evaluate(
            [trusted],
            host_events={"event-3": {"event_id": "event-3", "type": "skill.loaded", "capability_id": "skill:target", "scope": "project", "version_id": "v3"}},
        )
        self.assertEqual(report.summary["selection_truth"], "HOST_OBSERVED")
        self.assertEqual(next(metric for metric in report.metrics if metric.name == "recall").value, 1.0)

    def test_fixture_runner_marks_artifact_mutation_unknown(self) -> None:
        no_skill = self.root / "no-skill"
        baseline = self.root / "baseline"
        candidate = self.root / "candidate"
        self._arm(no_skill, "none", skill=False)
        self._arm(baseline, "baseline")
        self._arm(candidate, "candidate", mutate=True)
        report = ThreeArmQualityRunner().run(
            self._cases(),
            {"no_skill": no_skill, "baseline": baseline, "candidate": candidate},
            lambda _case, _output: {"passed": True},
        )
        candidate_results = [item for item in report.results if item["arm"] == "candidate"]
        self.assertTrue(all(item["task_result"] == "unknown" for item in candidate_results))
        self.assertEqual(report.summary["verdict"], "BLOCKED_ENV")

    def test_real_runner_rejects_skill_leak_in_no_skill_arm(self) -> None:
        leaked = self.root / "leaked"
        self._arm(leaked, "leaked")
        with self.assertRaises(ValueError):
            RealCodexThreeArmRunner._validate(self._cases(), {"no_skill": leaked, "baseline": leaked, "candidate": self.root})

    def test_no_skill_activation_is_not_allowed_to_claim_verified(self) -> None:
        leaked = self.root / "leaked"
        self._arm(leaked, "leaked")
        runner = CodexRealTaskRunner(self.workspace, codex_command="missing-codex", timeout=1)
        record = runner.activate_artifact(
            experiment_id="e", pair_id="p", case_id="c", side="no_skill",
            artifact_root=leaked, expected_artifact_hash=runner._artifact_hash(leaked),
        )
        self.assertEqual(record.activation_status, "MISMATCH")
        self.assertEqual(record.reason, "no_skill_skill_leak")

    def test_observer_close_does_not_upgrade_pending_request_to_complete(self) -> None:
        ingestor = TraceIngestor(self.workspace)
        recorder = McpTraceRecorder(ingestor, server_name="fixture", transport="stdio")
        recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}})
        report = recorder.close(status="ok")
        self.assertEqual(report.session_close_status, "PARTIAL")
        self.assertEqual(report.pending_request_count, 1)
        self.assertEqual(ingestor.traces.get(report.trace_id).observation_completeness, "partial")

        recorder2 = McpTraceRecorder(ingestor, server_name="fixture", transport="stdio")
        recorder2.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "x"}})
        recorder2.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        report2 = recorder2.close(status="ok")
        self.assertEqual(report2.session_close_status, "COMPLETE")
        self.assertEqual(ingestor.traces.get(report2.trace_id).observation_completeness, "complete")

    def test_real_runner_validation_keeps_three_arm_identity_rules(self) -> None:
        no_skill = self.root / "no-skill"
        baseline = self.root / "baseline"
        candidate = self.root / "candidate"
        self._arm(no_skill, "none", skill=False)
        self._arm(baseline, "baseline")
        self._arm(candidate, "candidate")
        frozen = RealCodexThreeArmRunner._validate(self._cases(), {"no_skill": no_skill, "baseline": baseline, "candidate": candidate})
        self.assertEqual(set(frozen), {"no_skill", "baseline", "candidate"})
        self.assertNotEqual(frozen["baseline"][1], frozen["candidate"][1])


if __name__ == "__main__":
    unittest.main()
