import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    ArtifactActivationRecord,
    CodexRealTaskRunner,
    FrozenComparisonManifest,
    M1Database,
    ReleaseObservationService,
    Trace,
    TraceStore,
    codex_preflight,
)
from shutu_workshop.release import artifact_tree_hash


SERVER_TEMPLATE = r'''
import json
import sys

LABEL = __LABEL__
TOOLS = [{"name": "echo", "description": LABEL, "inputSchema": {"type": "object"}}]

for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    method = request.get("method")
    if method in {"initialized", "notifications/initialized"}:
        continue
    result = {}
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "serverInfo": {"name": LABEL, "version": "1"}, "capabilities": {"tools": {}}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {})
        result = {"content": [{"type": "text", "text": LABEL + ":" + str(arguments.get("text", ""))}]}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}) + "\n")
    sys.stdout.flush()
'''


class M61RealAgentClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.home = root / "codex-home"
        self.baseline = root / "baseline"
        self.candidate = root / "candidate"
        for side, label in ((self.baseline, "baseline"), (self.candidate, "candidate")):
            side.mkdir(parents=True)
            (side / "server.py").write_text(SERVER_TEMPLATE.replace("__LABEL__", repr(label)), encoding="utf-8")
        self.runner = CodexRealTaskRunner(self.workspace, codex_home=self.home, timeout=5)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _activation(self, side: str, root: Path, expected: str | None = None):
        return self.runner.activate_artifact(
            experiment_id="experiment-m61",
            pair_id="pair-m61",
            case_id=f"case-{side}",
            side=side,
            artifact_root=root,
            expected_artifact_hash=expected or artifact_tree_hash(root),
            capability_version_id=f"{side}-v1",
            server_command=(sys.executable, str(root / "server.py")),
            server_name=f"m61-{side}",
            codex_home=self.home,
        )

    def test_real_side_activation_uses_bytes_and_real_mcp_exchange(self) -> None:
        baseline = self._activation("baseline", self.baseline)
        candidate = self._activation("candidate", self.candidate)
        self.assertIsInstance(baseline, ArtifactActivationRecord)
        self.assertEqual(baseline.activation_status, "VERIFIED")
        self.assertEqual(candidate.activation_status, "VERIFIED")
        self.assertNotEqual(baseline.actual_prelaunch_hash, candidate.actual_prelaunch_hash)
        self.assertTrue(baseline.activation_evidence_refs)
        self.assertNotEqual(baseline.mcp_server_instance_id, candidate.mcp_server_instance_id)
        events = TraceStore(self.runner.db).list_events()
        self.assertGreaterEqual(sum(event.type == "mcp.tool_call" for event in events), 2)
        self.assertGreaterEqual(sum(event.type == "mcp.tool_result" for event in events), 2)

    def test_declared_hash_mismatch_and_post_freeze_mutation_are_rejected(self) -> None:
        mismatch = self._activation("candidate", self.candidate, "b" * 64)
        self.assertEqual(mismatch.activation_status, "MISMATCH")
        self.assertEqual(mismatch.activation_evidence_refs, ())
        expected = artifact_tree_hash(self.candidate)
        self.assertEqual(self._activation("candidate", self.candidate, expected).activation_status, "VERIFIED")
        (self.candidate / "server.py").write_text(
            (self.candidate / "server.py").read_text(encoding="utf-8") + "\n# changed after freeze\n",
            encoding="utf-8",
        )
        changed = self._activation("candidate", self.candidate, expected)
        self.assertEqual(changed.activation_status, "MISMATCH")

    def test_hash_only_compatibility_stays_unverified(self) -> None:
        record = self.runner.activate_artifact(
            experiment_id="e",
            pair_id="p",
            case_id="c",
            side="baseline",
            artifact_root=None,
            expected_artifact_hash="a" * 64,
        )
        self.assertEqual(record.activation_status, "UNVERIFIED")
        self.assertEqual(record.reason, "artifact_root_not_supplied")

    def test_hash_only_runner_cannot_persist_a_success(self) -> None:
        blocked_runner = CodexRealTaskRunner(self.workspace / "hash-only", codex_command="cmd.exe", timeout=1)
        evidence = blocked_runner.run(
            case_id="hash-only",
            task_input={"instruction": "local only"},
            baseline_or_candidate="candidate",
            artifact_hash="a" * 64,
        )
        self.assertEqual(evidence.activation_status, "UNVERIFIED")
        self.assertNotEqual(evidence.execution_evidence_tier, "REAL_TASK_EXECUTED")
        with blocked_runner.db.connection() as connection:
            row = connection.execute(
                "SELECT activation_status, execution_evidence_tier FROM real_agent_runs WHERE execution_manifest_hash = ?",
                (evidence.execution_manifest_hash,),
            ).fetchone()
        self.assertEqual(tuple(row), ("UNVERIFIED", "REAL_AGENT_OBSERVED"))

    def test_run_pair_wires_independent_side_roots_and_commands(self) -> None:
        calls = []

        def fake_run(**kwargs):
            calls.append(kwargs)
            return kwargs

        original = self.runner.run
        self.runner.run = fake_run  # type: ignore[method-assign]
        try:
            results = self.runner.run_pair(
                [
                    {
                        "case_id": "target",
                        "task_input": {"text": "x"},
                        "baseline_artifact_hash": "a" * 64,
                        "candidate_artifact_hash": "b" * 64,
                        "baseline_artifact_root": self.baseline,
                        "candidate_artifact_root": self.candidate,
                    }
                ],
                pair_id="p",
                experiment_id="e",
            )
        finally:
            self.runner.run = original  # type: ignore[method-assign]
        self.assertEqual(len(results), 2)
        self.assertEqual([call["baseline_or_candidate"] for call in calls], ["baseline", "candidate"])
        self.assertNotEqual(calls[0]["artifact_root"], calls[1]["artifact_root"])
        self.assertNotEqual(calls[0]["mcp_server_command"], calls[1]["mcp_server_command"])
        self.assertIn("baseline", calls[0]["codex_home_override"])
        self.assertIn("candidate", calls[1]["codex_home_override"])

    def test_preflight_is_redacted_and_reports_missing_provider_without_user_home(self) -> None:
        result = codex_preflight(
            self.workspace,
            codex_command="codex-command-that-does-not-exist",
            codex_home=self.home,
            cwd=self.workspace,
            timeout=1,
        )
        self.assertEqual(result["home_scope"], "isolated")
        self.assertEqual(result["raw_output_persisted"], False)
        self.assertEqual(result["credentials_persisted"], False)
        self.assertIn(result["provider_status"], {"BLOCKED_ENV", "UNVERIFIED"})
        self.assertNotIn("Missing bearer", json.dumps(result))

    def test_frozen_pre_release_identity_is_linked_without_trace_rewrite(self) -> None:
        manifest = ReleaseObservationService(self.workspace).freeze_experiment_identity(
            FrozenComparisonManifest(
                experiment_id="exp-before-release",
                baseline_artifact_hash="a" * 64,
                candidate_artifact_hash="b" * 64,
                baseline_capability_version_id="baseline-v1",
                candidate_capability_version_id="candidate-v1",
                task_suite_id="suite",
                task_suite_hash="suite-hash",
                case_input_summaries={"case-1": "input-digest"},
            )
        )
        self.assertEqual(manifest.release_id, "")
        trace = Trace(
            trace_id="before-trace",
            workspace_id="w",
            adapter_id="codex",
            started_at="2026-01-01T00:00:00+00:00",
            source="codex-cli-jsonl",
            metadata={
                "experiment_id": manifest.experiment_id,
                "comparison_manifest_hash": manifest.manifest_hash,
                "artifact_hash": manifest.baseline_artifact_hash,
                "capability_version_id": manifest.baseline_capability_version_id,
                "source_evidence_tier": "REAL_AGENT_OBSERVED",
            },
        )
        TraceStore(self.runner.db).ingest_batch([trace], [])
        stored_before = TraceStore(self.runner.db).get("before-trace")
        self.assertNotIn("release_id", stored_before.metadata)
        with self.runner.db.transaction() as connection:
            connection.execute("INSERT INTO release_plans(plan_id, proposal_id, evaluation_run_id, target_scope, target_skill_name, target_root, candidate_root, baseline_root, baseline_artifact_hash, candidate_artifact_hash, target_current_hash, evaluation_manifest_hash, state, plan_hash, plan_json, created_at, updated_at, row_version) VALUES ('plan', 'proposal', 'eval', 'isolated_workspace', 'demo', 'target', 'candidate', 'baseline', ?, ?, ?, 'manifest', 'completed', 'plan-hash', '{}', '2026-01-01', '2026-01-01', 0)", ('a' * 64, 'b' * 64, 'a' * 64))
            connection.execute("INSERT INTO release_links(release_id, plan_id, idempotency_key, status, target_scope, target_skill_name, candidate_artifact_hash, baseline_artifact_hash, target_current_hash, capability_version_id, m0_operation_id, m0_version_id, rollback_version_id, created_at, updated_at, payload_json) VALUES ('release', 'plan', 'idempotency', 'completed', 'isolated_workspace', 'demo', ?, ?, ?, '', '', 'candidate-v1', '', '2026-01-01', '2026-01-01', '{}')", ('b' * 64, 'a' * 64, 'a' * 64))
        linked = ReleaseObservationService(self.workspace).link_experiment_identity(manifest.experiment_id, "release")
        self.assertEqual(linked.release_id, "release")
        after = TraceStore(self.runner.db).get("before-trace")
        self.assertEqual(after.metadata, stored_before.metadata)
        with self.runner.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM experiment_release_links").fetchone()[0], 1)

    def test_manifest_records_suite_and_activation_identity(self) -> None:
        manifest = FrozenComparisonManifest(
            experiment_id="e",
            task_suite_hash="suite-hash",
            case_input_summaries={"target": "digest"},
            baseline_mcp_server_command_fingerprint="a",
            candidate_mcp_server_command_fingerprint="b",
            oracle_id="oracle",
            oracle_version="1",
            sandbox_policy="read-only",
            activation_evidence_refs=("event-a", "event-b"),
        )
        restored = FrozenComparisonManifest.from_dict(manifest.to_dict())
        self.assertEqual(restored.manifest_hash, manifest.manifest_hash)
        self.assertEqual(restored.activation_evidence_refs, ("event-a", "event-b"))

    def test_preflight_cli_does_not_require_context7_or_persist_secrets(self) -> None:
        result = codex_preflight(self.workspace, codex_command="missing-codex", codex_home=self.home, timeout=1)
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("auth.json", encoded)
        self.assertNotIn("Authorization", encoded)


if __name__ == "__main__":
    unittest.main()
