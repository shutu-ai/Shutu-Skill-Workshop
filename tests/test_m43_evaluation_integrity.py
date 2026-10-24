import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from shutu_workshop import (
    CapabilityRegistry,
    DiagnosisService,
    EvaluationService,
    EvolutionDiagnosisService,
    ProposalService,
    Trace,
    TraceEvent,
    TraceIngestor,
)
from shutu_workshop.evolution import EvaluationSuite, TaskCase
from shutu_workshop.evolution import local_fixture_candidate_payload, local_fixture_suite, LocalFixtureRunner


class M43EvaluationIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
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
        report = EvolutionDiagnosisService(self.workspace).diagnose(issue_id)
        proposal_service = ProposalService(self.workspace)
        proposal = proposal_service.create_from_diagnosis(issue_id, report.diagnosis_id)[0]
        ready = proposal_service.review(proposal.proposal_id, "ready-for-review")
        self.proposal = proposal_service.review(ready.proposal_id, "accept-for-eval")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_candidate_execution_is_not_improved(self) -> None:
        result = EvaluationService(self.workspace).run(self.proposal.proposal_id, mode="fixture")
        self.assertEqual(result.verdict.verdict, "INCONCLUSIVE")
        candidate_results = [item for item in result.results if item.side == "candidate"]
        self.assertTrue(candidate_results)
        self.assertTrue(all(item.task_success is None for item in candidate_results))
        self.assertTrue(all(item.oracle_verdict == "unknown" for item in candidate_results))

    def test_execution_inputs_change_cache_identity(self) -> None:
        suite = EvaluationSuite(
            suite_id="m43-cache-suite",
            name="cache identity",
            suite_version="1",
            cases=(
                TaskCase(
                    case_id="case",
                    input_ref={"kind": "fixture", "task_input": {"value": "one"}},
                    expected_behavior={"expected_output": {"value": "good"}},
                    baseline_output={"value": "bad"},
                ),
            ),
        )
        service = EvaluationService(self.workspace)
        first = service.run(
            self.proposal.proposal_id,
            suite=suite,
            candidate_outputs={"case": {"value": "good"}},
            baseline_outputs={"case": {"value": "bad"}},
        )
        second = service.run(
            self.proposal.proposal_id,
            suite=suite,
            candidate_outputs={"case": {"value": "changed"}},
            baseline_outputs={"case": {"value": "bad"}},
        )
        self.assertNotEqual(first.run["run_id"], second.run["run_id"])
        self.assertNotEqual(first.run["execution_manifest_hash"], second.run["execution_manifest_hash"])

    def test_missing_holdout_blocks_positive_verdict(self) -> None:
        suite = EvaluationSuite(
            suite_id="m43-holdout-suite",
            name="holdout isolation",
            suite_version="1",
            cases=(
                TaskCase(
                    case_id="holdout",
                    input_ref={"kind": "fixture", "task_input": {"value": "hidden"}},
                    expected_behavior={"expected_output": {"value": "hidden"}},
                    baseline_output={"value": "hidden"},
                    holdout=True,
                ),
            ),
        )
        result = EvaluationService(self.workspace).run(
            self.proposal.proposal_id,
            suite=suite,
            candidate_outputs={},
            baseline_outputs={},
        )
        self.assertEqual(result.verdict.verdict, "INCONCLUSIVE")
        holdout = next(item for item in result.results if item.side == "candidate")
        self.assertEqual(holdout.oracle_verdict, "unknown")
        self.assertIsNone(holdout.task_success)

    def test_direct_expected_outputs_are_imported_not_execution_evidence(self) -> None:
        suite = local_fixture_suite()
        expected = {case.case_id: case.expected_behavior["expected_output"] for case in suite.cases}
        result = EvaluationService(self.workspace).run(
            self.proposal.proposal_id,
            suite=suite,
            candidate_outputs=expected,
            baseline_outputs=expected,
        )
        self.assertEqual(result.verdict.verdict, "INCONCLUSIVE")
        self.assertEqual(result.verdict.evidence_tier, "SYNTHETIC_OR_IMPORTED")
        self.assertTrue(all(item.provenance == "imported_unverified" for item in result.results))

    def test_runner_receives_no_oracle_answer_for_holdout(self) -> None:
        seen: list[dict[str, object]] = []

        class RecordingRunner(LocalFixtureRunner):
            def execute(self, case_input, candidate):
                seen.append(case_input.to_dict())
                return super().execute(case_input, candidate)

        proposal_service = ProposalService(self.workspace)
        revised = proposal_service.revise(
            self.proposal.proposal_id,
            {**self.proposal.payload, **local_fixture_candidate_payload()},
        )
        ready = proposal_service.review(revised.proposal_id, "ready-for-review", expected_row_version=revised.row_version)
        accepted = proposal_service.review(ready.proposal_id, "accept-for-eval", expected_row_version=ready.row_version)
        result = EvaluationService(self.workspace, runner=RecordingRunner()).run(
            accepted.proposal_id,
            suite=local_fixture_suite(),
        )
        self.assertEqual(result.verdict.verdict, "IMPROVED")
        holdout = next(item for item in seen if item["case_id"] == "mcp_echo_holdout")
        self.assertNotIn("expected_output", str(holdout))
        self.assertEqual(sum(item.provenance == "fixture_executed" for item in result.results), len(result.results))

    def test_same_manifest_is_idempotent_under_concurrent_submission(self) -> None:
        proposal_service = ProposalService(self.workspace)
        revised = proposal_service.revise(
            self.proposal.proposal_id,
            {**self.proposal.payload, **local_fixture_candidate_payload()},
        )
        ready = proposal_service.review(revised.proposal_id, "ready-for-review", expected_row_version=revised.row_version)
        accepted = proposal_service.review(ready.proposal_id, "accept-for-eval", expected_row_version=ready.row_version)

        def run_once() -> str:
            return EvaluationService(self.workspace).run(accepted.proposal_id, suite=local_fixture_suite()).run["run_id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            run_ids = list(pool.map(lambda _: run_once(), (1, 2)))
        self.assertEqual(run_ids[0], run_ids[1])
        with EvaluationService(self.workspace).db.connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM evaluation_runs WHERE execution_manifest_hash <> ''"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
