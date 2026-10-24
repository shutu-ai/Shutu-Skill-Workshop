from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    FrozenComparisonManifest,
    M1Database,
    OptimizationFeedbackService,
    ObservationWindow,
    ReleaseObservationService,
    Trace,
    TraceStore,
)
from shutu_workshop.adapters.base import CaptureResult
from shutu_workshop.adapters.real_agent import CodexRealTaskRunner
from shutu_workshop.observers.mcp.common import correlate_events
from shutu_workshop.models import TraceEvent


class M6RealWorldValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.db = M1Database(self.workspace)
        self.release_id = "release-m6"
        self.baseline_hash = "b" * 64
        self.candidate_hash = "c" * 64
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO release_plans(
                    plan_id, proposal_id, evaluation_run_id, target_scope, target_skill_name,
                    target_root, candidate_root, baseline_root, baseline_artifact_hash,
                    candidate_artifact_hash, target_current_hash, evaluation_manifest_hash,
                    state, plan_hash, plan_json, created_at, updated_at, row_version
                ) VALUES ('plan-m6', 'proposal-m6', 'eval-m6', 'isolated_workspace', 'demo',
                    'target', 'candidate', 'baseline', ?, ?, ?, 'eval-manifest',
                    'completed', 'plan-hash', '{}', '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', 0)
                """,
                (self.baseline_hash, self.candidate_hash, self.baseline_hash),
            )
            connection.execute(
                """
                INSERT INTO release_links(
                    release_id, plan_id, idempotency_key, status, target_scope,
                    target_skill_name, candidate_artifact_hash, baseline_artifact_hash,
                    target_current_hash, capability_version_id, m0_operation_id,
                    m0_version_id, rollback_version_id, created_at, updated_at, payload_json
                ) VALUES (?, 'plan-m6', 'idempotency-m6', 'completed', 'isolated_workspace',
                    'demo', ?, ?, ?, '', '', 'candidate-v1', '',
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '{}')
                """,
                (self.release_id, self.candidate_hash, self.baseline_hash, self.baseline_hash),
            )
        self.manifest = FrozenComparisonManifest(
            release_id=self.release_id,
            baseline_artifact_hash=self.baseline_hash,
            candidate_artifact_hash=self.candidate_hash,
            baseline_capability_version_id="baseline-v1",
            candidate_capability_version_id="candidate-v1",
            task_suite_id="m6-suite",
            task_category="m6-task",
            environment_fingerprint="env-m6",
            runner_id="codex-runner",
            min_samples=2,
        )
        self.observer = ReleaseObservationService(self.workspace)
        self.manifest = self.observer.freeze_manifest(self.release_id, self.manifest)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _trace(
        self,
        trace_id: str,
        phase: str,
        artifact_hash: str,
        task_result: str,
        started_at: str,
        *,
        capability_version_id: str,
        release_id: str = "",
        manifest_hash: str = "",
        source_tier: str = "REAL_AGENT_OBSERVED",
        task_category: str = "m6-task",
        runner_id: str = "codex-runner",
    ) -> Trace:
        return Trace(
            trace_id=trace_id,
            workspace_id="m6-workspace",
            adapter_id="codex",
            adapter_version="0.163.0",
            started_at=started_at,
            status="ok" if task_result == "success" else "failed" if task_result == "failed" else "unknown",
            environment_fingerprint="env-m6",
            source="codex-cli-jsonl",
            observation_completeness="complete",
            task_result=task_result,
            metadata={
                "release_id": release_id,
                "release_phase": phase,
                "artifact_hash": artifact_hash,
                "capability_version_id": capability_version_id,
                "comparison_manifest_hash": manifest_hash,
                "task_category": task_category,
                "runner_id": runner_id,
                "task_suite_id": "m6-suite",
                "source_evidence_tier": source_tier,
                "real_agent": source_tier == "REAL_AGENT_OBSERVED",
            },
        )

    def test_before_binding_does_not_require_future_release_id_and_rates_use_denominator(self) -> None:
        traces: list[Trace] = []
        for index in range(100):
            traces.append(
                self._trace(
                    f"before-{index}",
                    "before",
                    self.baseline_hash,
                    "failed" if index < 10 else "success",
                    f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
                    capability_version_id="baseline-v1",
                    manifest_hash=self.manifest.manifest_hash,
                )
            )
        for index in range(200):
            traces.append(
                self._trace(
                    f"after-{index}",
                    "after",
                    self.candidate_hash,
                    "failed" if index < 15 else "success",
                    f"2026-01-02T00:{index // 60:02d}:{index % 60:02d}+00:00",
                    capability_version_id="candidate-v1",
                    release_id=self.release_id,
                    manifest_hash=self.manifest.manifest_hash,
                )
            )
        TraceStore(self.db).ingest_batch(traces, [])
        result = self.observer.observe(
            self.release_id,
            ObservationWindow(
                before_start="2026-01-01T00:00:00+00:00",
                before_end="2026-01-01T02:00:00+00:00",
                after_start="2026-01-02T00:00:00+00:00",
                after_end="2026-01-02T04:00:00+00:00",
                min_samples=2,
                comparison_manifest_id=self.manifest.manifest_id,
            ),
        )
        self.assertEqual(result.conclusion, "NO_CLEAR_CHANGE")
        self.assertEqual(result.summary["before"]["n_total"], 100)
        self.assertEqual(result.summary["after"]["n_total"], 200)
        self.assertAlmostEqual(result.summary["before"]["failure_rate"], 0.1)
        self.assertAlmostEqual(result.summary["after"]["failure_rate"], 0.075)
        self.assertNotIn("release_id_mismatch", result.summary["before"]["excluded"])

    def test_same_tool_without_manifest_or_capability_is_not_verified_before(self) -> None:
        TraceStore(self.db).ingest_batch(
            [
                self._trace(
                    "unverified-before",
                    "before",
                    self.baseline_hash,
                    "success",
                    "2026-01-01T00:00:00+00:00",
                    capability_version_id="",
                    manifest_hash="",
                )
            ],
            [],
        )
        result = self.observer.observe(
            self.release_id,
            ObservationWindow(
                before_start="2026-01-01T00:00:00+00:00",
                before_end="2026-01-01T01:00:00+00:00",
                after_start="2026-01-02T00:00:00+00:00",
                after_end="2026-01-02T01:00:00+00:00",
                min_samples=1,
                comparison_manifest_id=self.manifest.manifest_id,
            ),
        )
        self.assertEqual(result.conclusion, "INSUFFICIENT_EVIDENCE")
        self.assertGreater(result.summary["before"]["excluded"].get("capability_version_mismatch", 0), 0)

    def test_simulation_feedback_is_demo_only_and_late_trace_creates_revision(self) -> None:
        window = ObservationWindow(
            before_start="2026-01-03T00:00:00+00:00",
            before_end="2026-01-03T01:00:00+00:00",
            after_start="2026-01-04T00:00:00+00:00",
            after_end="2026-01-04T01:00:00+00:00",
            min_samples=1,
            comparison_manifest_id=self.manifest.manifest_id,
        )
        self.observer.record_simulated_trace(
            self.release_id,
            phase="before",
            artifact_hash=self.baseline_hash,
            task_result="failed",
            started_at="2026-01-03T00:15:00+00:00",
            task_category="m6-task",
            environment_fingerprint="env-m6",
            task_suite_id="m6-suite",
            comparison_manifest_hash=self.manifest.manifest_hash,
            runner_id="codex-runner",
        )
        self.observer.record_simulated_trace(
            self.release_id,
            phase="after",
            artifact_hash=self.candidate_hash,
            task_result="success",
            started_at="2026-01-04T00:15:00+00:00",
            task_category="m6-task",
            environment_fingerprint="env-m6",
            task_suite_id="m6-suite",
            comparison_manifest_hash=self.manifest.manifest_hash,
            runner_id="codex-runner",
        )
        result = self.observer.observe(self.release_id, window)
        self.assertEqual(result.signal, "DEMONSTRATION_ONLY")
        feedback_service = OptimizationFeedbackService(self.workspace)
        first = feedback_service.record(result)
        self.assertEqual(first.feedback_state, "DEMONSTRATION_ONLY")
        self.assertEqual(feedback_service.record(result).feedback_id, first.feedback_id)
        self.observer.record_simulated_trace(
            self.release_id,
            phase="after",
            artifact_hash=self.candidate_hash,
            task_result="failed",
            started_at="2026-01-04T00:30:00+00:00",
            task_category="m6-task",
            environment_fingerprint="env-m6",
            task_suite_id="m6-suite",
            comparison_manifest_hash=self.manifest.manifest_hash,
            runner_id="codex-runner",
        )
        revised = self.observer.observe(self.release_id, window)
        second = feedback_service.record(revised)
        self.assertEqual(revised.observation_id, result.observation_id)
        self.assertGreater(second.revision, first.revision)

    def test_jsonrpc_ids_are_scoped_across_sources(self) -> None:
        def event(source: str, connection: str) -> TraceEvent:
            return TraceEvent(
                trace_id=f"trace-{source}-{connection}",
                seq=0,
                type="mcp.tool_call",
                event_id=f"{source}-{connection}",
                request_id="1",
                observation_source=source,
                source_metadata={
                    "correlation_scope": {
                        "workspace_id": "w",
                        "run_id": f"run-{source}",
                        "mcp_server": "fixture",
                        "mcp_connection": connection,
                        "transport": "stdio",
                        "request_id": "1",
                    }
                },
            )

        groups = correlate_events([event("mcp_observer", "a"), event("mcp_observer", "b")])
        self.assertEqual(sum(len(group) > 1 for group in groups), 0)

    def test_real_runner_marks_provider_auth_as_blocked_environment(self) -> None:
        capture = CaptureResult(
            exit_code=1,
            stdout='{"type":"error","message":"401 Unauthorized: Missing bearer"}',
            stderr_summary="",
        )
        self.assertEqual(CodexRealTaskRunner._environment_blocked(capture, []), (True, "provider_or_transport"))


if __name__ == "__main__":
    unittest.main()
