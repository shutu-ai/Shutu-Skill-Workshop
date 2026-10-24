import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    CapabilityRegistry,
    DiagnosisService,
    EvaluationService,
    EvolutionDiagnosisService,
    FileArtifactService,
    FileBackedIsolatedRunner,
    ProposalService,
    ReleaseObservationService,
    ReleasePlanService,
    Trace,
    TraceEvent,
    TraceIngestor,
    artifact_tree_hash,
)
from shutu_workshop.evolution import local_fixture_candidate_payload, local_fixture_suite
from shutu_workshop.release import ArtifactError, ObservationWindow, isolated_skill_suite


class M5ReleaseObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.isolated = root / "isolated"
        self.target = self.isolated / "demo"
        self.target.mkdir(parents=True)
        (self.target / "scripts").mkdir()
        (self.target / "SKILL.md").write_text("---\nname: demo\n---\n\nLocal test skill.\n", encoding="utf-8")
        (self.target / "scripts" / "validator.py").write_text(
            """import json
import sys

value = json.load(sys.stdin)
path = str(value.get("path", ""))
if "/" in path or "\\\\" in path or ".." in path:
    output = {"valid": False, "error": "unsafe_path"}
else:
    output = {"valid": True, "content_hash": value.get("content", "")}
print(json.dumps(output, sort_keys=True))
""",
            encoding="utf-8",
        )
        registry = CapabilityRegistry(self.workspace)
        version = registry.register_mcp_tool_snapshot(
            "fixture",
            {"name": "echo", "description": "safe echo", "inputSchema": {"type": "object"}},
            version_id="fixture-v1",
        )
        ingestor = TraceIngestor(self.workspace)
        ingestor.start(Trace(trace_id="issue-trace", workspace_id="w", adapter_id="mcp", status="failed"))
        ingestor.append(
            TraceEvent(
                trace_id="issue-trace",
                seq=0,
                type="mcp.tool_result",
                actor="tool",
                event_id="issue-event",
                capability_id=version.capability_id,
                capability_version_id=version.version_id,
                status="error",
                tool_name="echo",
                payload_json={"result": {"isError": True}},
                source_metadata={"correlation_scope": {"workspace_id": "w", "mcp_server": "fixture"}},
            )
        )
        issue_id = DiagnosisService(self.workspace).analyze_trace("issue-trace").issue_ids[0]
        diagnosis = EvolutionDiagnosisService(self.workspace).diagnose(issue_id)
        proposals = ProposalService(self.workspace).create_from_diagnosis(issue_id, diagnosis.diagnosis_id)
        service = ProposalService(self.workspace)
        ready = service.review(proposals[0].proposal_id, "ready-for-review")
        self.proposal = service.review(ready.proposal_id, "accept-for-eval", expected_row_version=ready.row_version)

        self.patch = {
            "path": "scripts/validator.py",
            "old_string": 'if "/" in path or "\\\\" in path or ".." in path:',
            "new_string": 'if path.startswith("/") or "\\\\" in path or ".." in path:',
            "reason": "allow bounded docs paths while retaining traversal rejection",
        }
        revised = service.revise(
            self.proposal.proposal_id,
            {**self.proposal.payload, "file_patch": [self.patch]},
            expected_row_version=self.proposal.row_version,
            category="skill_script",
        )
        ready = service.review(revised.proposal_id, "ready-for-review", expected_row_version=revised.row_version)
        self.proposal = service.review(ready.proposal_id, "accept-for-eval", expected_row_version=ready.row_version)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _materialize_and_evaluate(self):
        candidate_root = self.isolated / "demo-candidate"
        manifest = FileArtifactService(self.workspace).materialize(
            self.proposal,
            self.target,
            candidate_root,
            isolated_root=self.isolated,
        )
        runner = FileBackedIsolatedRunner(self.target, candidate_root, allowed_root=self.isolated)
        evaluation = EvaluationService(self.workspace, runner=runner).run(
            self.proposal.proposal_id,
            suite=isolated_skill_suite(),
            mode="isolated",
        )
        return manifest, evaluation

    def test_file_backed_evaluation_plan_approval_m0_release_and_rollback(self) -> None:
        manifest, evaluation = self._materialize_and_evaluate()
        self.assertEqual(evaluation.verdict.verdict, "IMPROVED")
        self.assertEqual(evaluation.verdict.evidence_tier, "ISOLATED_ARTIFACT_EXECUTED")
        self.assertTrue(FileArtifactService.verify(manifest))

        plans = ReleasePlanService(self.workspace)
        plan = plans.create_plan(
            self.proposal.proposal_id,
            evaluation.run["run_id"],
            manifest,
            self.isolated,
            target_skill_name="demo",
        )
        self.assertEqual(plan.state, "eligible")
        self.assertEqual(
            plans.eligibility.assess(
                plan.proposal_id,
                plan.evaluation_run_id,
                manifest,
                plan.target_root,
                "isolated_workspace",
            ).decision,
            "NEEDS_EVIDENCE",
        )
        approval = plans.approve(plan.plan_id, reviewer="tester", expected_plan_hash=plan.plan_hash)
        self.assertEqual(approval.status, "approved")
        self.assertEqual(plans.verify(plan.plan_id).decision, "ELIGIBLE_ISOLATED")
        release = plans.execute(plan.plan_id, isolated_root=self.isolated)
        self.assertEqual(release.status, "completed")
        self.assertEqual(release.candidate_artifact_hash, manifest.candidate_artifact_hash)
        self.assertEqual(plans.execute(plan.plan_id, isolated_root=self.isolated).release_id, release.release_id)
        self.assertTrue(FileArtifactService.verify(manifest))

        baseline_version_id = release.payload["baseline_version_id"]
        rolled = plans.rollback(release.release_id, baseline_version_id)
        self.assertEqual(rolled.status, "rolled_back")
        self.assertEqual(manifest.baseline_artifact_hash, artifact_tree_hash(self.target))

    def test_candidate_mutation_and_advisory_are_blocked(self) -> None:
        manifest, evaluation = self._materialize_and_evaluate()
        candidate = Path(manifest.candidate_root)
        (candidate / "SKILL.md").write_text((candidate / "SKILL.md").read_text(encoding="utf-8") + "x", encoding="utf-8")
        decision = ReleasePlanService(self.workspace).eligibility.assess(
            self.proposal.proposal_id,
            evaluation.run["run_id"],
            manifest,
            self.target,
            "isolated_workspace",
        )
        self.assertEqual(decision.decision, "DENIED")
        self.assertIn("CANDIDATE_ARTIFACT_MUTATED", decision.reason_codes)
        with self.assertRaises(ArtifactError):
            FileArtifactService.patch_payload({"category": "mcp_schema", "payload": {"file_patch": [self.patch]}})

    def test_live_manifest_row_is_returned_as_in_progress(self) -> None:
        proposal_service = ProposalService(self.workspace)
        revised = proposal_service.revise(
            self.proposal.proposal_id,
            {**self.proposal.payload, **local_fixture_candidate_payload()},
            expected_row_version=self.proposal.row_version,
            category="skill_script",
        )
        ready = proposal_service.review(revised.proposal_id, "ready-for-review", expected_row_version=revised.row_version)
        accepted = proposal_service.review(ready.proposal_id, "accept-for-eval", expected_row_version=ready.row_version)
        service = EvaluationService(self.workspace)
        suite = local_fixture_suite()
        service.register_suite(suite)
        candidate = service._candidate(accepted)
        context = service._execution_context(candidate, suite, "fixture", candidate_outputs=None, baseline_outputs=None, metrics=None)
        self.assertTrue(context.cacheable)
        with service.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_runs(
                    run_id, suite_id, candidate_id, proposal_id, baseline_fingerprint,
                    candidate_fingerprint, suite_hash, mode, environment_fingerprint,
                    status, started_at, execution_manifest_hash, execution_manifest_json,
                    runner_id, runner_version, runner_config_hash, oracle_id, oracle_version,
                    baseline_artifact_hash, candidate_artifact_hash, evidence_tier,
                    lease_until, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    "eval_live",
                    suite.suite_id,
                    candidate.candidate_id,
                    accepted.proposal_id,
                    context.baseline_fingerprint,
                    context.candidate_fingerprint,
                    context.suite_hash,
                    "fixture",
                    context.environment_fingerprint,
                    "2026-10-24T00:00:00+00:00",
                    context.manifest_hash,
                    json.dumps(context.manifest, sort_keys=True),
                    context.runner_id,
                    context.runner_version,
                    context.runner_config_hash,
                    context.oracle_id,
                    context.oracle_version,
                    context.baseline.artifact_hash,
                    context.candidate.artifact_hash,
                    "STATIC_ONLY",
                    "2999-01-01T00:00:00+00:00",
                ),
            )
        report = service.run(accepted.proposal_id, suite=suite, mode="fixture")
        self.assertEqual(report.run["run_id"], "eval_live")
        self.assertEqual(report.run["status"], "running")
        self.assertIsNone(report.verdict)

    def test_no_after_trace_is_insufficient_and_recalculation_is_idempotent(self) -> None:
        manifest, evaluation = self._materialize_and_evaluate()
        plans = ReleasePlanService(self.workspace)
        plan = plans.create_plan(self.proposal.proposal_id, evaluation.run["run_id"], manifest, self.isolated, target_skill_name="demo")
        plans.approve(plan.plan_id, reviewer="tester", expected_plan_hash=plan.plan_hash)
        release = plans.execute(plan.plan_id, isolated_root=self.isolated)
        observer = ReleaseObservationService(self.workspace)
        window = ObservationWindow(
            before_start="2026-01-01T00:00:00+00:00",
            before_end="2026-01-02T00:00:00+00:00",
            after_start="2026-01-02T00:00:00+00:00",
            after_end="2026-01-03T00:00:00+00:00",
            min_samples=1,
        )
        insufficient = observer.observe(release.release_id, window)
        self.assertEqual(insufficient.conclusion, "INSUFFICIENT_EVIDENCE")
        after = observer.record_simulated_trace(
            release.release_id,
            phase="after",
            artifact_hash=manifest.candidate_artifact_hash,
            task_result="success",
            started_at="2026-01-02T12:00:00+00:00",
        )
        before = observer.record_simulated_trace(
            release.release_id,
            phase="before",
            artifact_hash=manifest.baseline_artifact_hash,
            task_result="failed",
            started_at="2026-01-01T12:00:00+00:00",
        )
        result = observer.observe(release.release_id, window)
        self.assertEqual(result.conclusion, "SIMULATION_ONLY")
        self.assertEqual(result.signal, "DEMONSTRATION_ONLY")
        self.assertEqual(observer.observe(release.release_id, window).observation_id, result.observation_id)
        self.assertNotEqual(after.trace_id, before.trace_id)
        regression_window = ObservationWindow(
            before_start="2026-01-04T00:00:00+00:00",
            before_end="2026-01-05T00:00:00+00:00",
            after_start="2026-01-05T00:00:00+00:00",
            after_end="2026-01-06T00:00:00+00:00",
            min_samples=1,
        )
        observer.record_simulated_trace(
            release.release_id,
            phase="before",
            artifact_hash=manifest.baseline_artifact_hash,
            task_result="success",
            started_at="2026-01-04T12:00:00+00:00",
        )
        observer.record_simulated_trace(
            release.release_id,
            phase="after",
            artifact_hash=manifest.candidate_artifact_hash,
            task_result="failed",
            started_at="2026-01-05T12:00:00+00:00",
        )
        self.assertEqual(observer.observe(release.release_id, regression_window).conclusion, "SIMULATION_ONLY")

    def test_subprocess_crash_after_m0_journal_is_reconciled_on_restart(self) -> None:
        manifest, evaluation = self._materialize_and_evaluate()
        plans = ReleasePlanService(self.workspace)
        plan = plans.create_plan(self.proposal.proposal_id, evaluation.run["run_id"], manifest, self.isolated, target_skill_name="demo")
        plans.approve(plan.plan_id, reviewer="tester", expected_plan_hash=plan.plan_hash)
        child = """
import os
from shutu_workshop.release.m0_bridge import M0ReleaseBridge
from shutu_workshop.release.plans import ReleasePlanService

service = ReleasePlanService({workspace!r})
original = M0ReleaseBridge.publish

def publish_then_crash(self, plan, manifest):
    original(self, plan, manifest)
    os._exit(79)

M0ReleaseBridge.publish = publish_then_crash
service.execute({plan_id!r}, isolated_root={isolated!r})
""".format(workspace=str(self.workspace), plan_id=plan.plan_id, isolated=str(self.isolated))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path.cwd() / "servers")
        crashed = subprocess.run([sys.executable, "-c", child], env=env, timeout=30, check=False)
        self.assertEqual(crashed.returncode, 79)
        restarted = ReleasePlanService(self.workspace).reconcile(plan.plan_id)
        self.assertIsNotNone(restarted)
        self.assertEqual(restarted.status, "completed")
        self.assertEqual(artifact_tree_hash(self.target), manifest.candidate_artifact_hash)


if __name__ == "__main__":
    unittest.main()
