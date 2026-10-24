import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

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
from shutu_workshop.evolution import (
    DiagnosisOutputError,
    EvidenceBundleBuilder,
    StaticValidator,
    local_fixture_suite,
    validate_evidence_refs,
)
from shutu_workshop.evolution.providers import ProviderResponse


class M4EvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.ingestor = TraceIngestor(self.workspace)
        registry = CapabilityRegistry(self.workspace)
        version = registry.register_mcp_tool_snapshot(
            "fixture",
            {"name": "echo", "description": "safe echo", "inputSchema": {"type": "object"}},
            version_id="fixture-v1",
        )
        self.ingestor.start(Trace(trace_id="issue-trace", workspace_id="w", adapter_id="mcp", status="failed"))
        self.ingestor.append(
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
                payload_json={"result": {"isError": True}, "token": "do-not-store"},
                source_metadata={"correlation_scope": {"workspace_id": "w", "mcp_server": "fixture"}},
            )
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_issue_to_verdict_is_bounded_idempotent_and_reviewed(self) -> None:
        diagnosis = DiagnosisService(self.workspace).analyze_trace("issue-trace")
        issue_id = diagnosis.issue_ids[0]
        bundle = EvidenceBundleBuilder(self.workspace, max_bytes=48 * 1024).build(
            issue_id,
            user_constraints=["do not modify production"],
        )
        self.assertTrue(validate_evidence_refs(self.workspace, bundle))
        self.assertNotIn("do-not-store", str(bundle.to_dict()))
        self.assertNotIn("verified_cause", str(bundle.to_dict()))

        advisor = EvolutionDiagnosisService(self.workspace)
        report = advisor.diagnose(issue_id, provider="fake")
        cached = advisor.diagnose(issue_id, provider="fake")
        self.assertEqual(report.diagnosis_id, cached.diagnosis_id)
        self.assertEqual(report.status, "review_required")
        self.assertNotIn("verified_cause", str(report.to_dict()))
        self.assertTrue(report.observed_facts[0]["evidence_refs"])
        self.assertIsNone(report.usage["cost"])

        proposals = ProposalService(self.workspace).create_from_diagnosis(issue_id, report.diagnosis_id)
        self.assertEqual(len(proposals), 1)
        proposal_service = ProposalService(self.workspace)
        accepted = proposal_service.review(
            proposals[0].proposal_id,
            "accept-for-eval",
            expected_row_version=proposals[0].row_version,
        )
        self.assertEqual(accepted.status, "accepted_for_evaluation")

        evaluation = EvaluationService(self.workspace)
        result = evaluation.run(accepted.proposal_id, suite=local_fixture_suite(), mode="fixture")
        self.assertIsNotNone(result.verdict)
        self.assertEqual(result.verdict.verdict, "IMPROVED")
        self.assertEqual(result.verdict.statistics["baseline_success"], 3)
        self.assertEqual(result.verdict.statistics["candidate_success"], 5)
        self.assertEqual(result.verdict.statistics["holdout_cases"], 1)
        self.assertEqual(proposal_service.get(accepted.proposal_id).status, "evaluated")
        replay = evaluation.run(accepted.proposal_id, suite=local_fixture_suite(), mode="fixture")
        self.assertEqual(replay.run["run_id"], result.run["run_id"])

    def test_candidate_regression_is_not_improvement(self) -> None:
        diagnosis = DiagnosisService(self.workspace).analyze_trace("issue-trace")
        issue_id = diagnosis.issue_ids[0]
        report = EvolutionDiagnosisService(self.workspace).diagnose(issue_id)
        proposal_service = ProposalService(self.workspace)
        proposal = proposal_service.create_from_diagnosis(issue_id, report.diagnosis_id)[0]
        accepted = proposal_service.review(proposal.proposal_id, "accept-for-eval")
        revised = proposal_service.revise(accepted.proposal_id, {**accepted.payload, "fixture_outputs": {"mcp_echo": {"bad": True}}})
        accepted_again = proposal_service.review(revised.proposal_id, "accept-for-eval", expected_row_version=revised.row_version)
        result = EvaluationService(self.workspace).run(accepted_again.proposal_id, suite=local_fixture_suite(), mode="fixture")
        self.assertEqual(result.verdict.verdict, "REGRESSED")

    def test_invalid_provider_output_and_static_candidate_are_rejected(self) -> None:
        issue_id = DiagnosisService(self.workspace).analyze_trace("issue-trace").issue_ids[0]

        class BadProvider:
            provider_id = "bad"
            model_id = "bad"

            def diagnose(self, request):
                return ProviderResponse(
                    content={"verified_cause": "certain", "observed_facts": []},
                    provider_id=self.provider_id,
                    model_id=self.model_id,
                    usage={},
                )

        with patch("shutu_workshop.evolution.diagnosis.provider_from_name", return_value=BadProvider()):
            with self.assertRaises(DiagnosisOutputError):
                EvolutionDiagnosisService(self.workspace).diagnose(issue_id, provider="bad")
        static = StaticValidator.validate({"category": "mcp_schema", "payload": {"path": "C:\\prod", "apply_proposal": True}})
        self.assertFalse(static["passed"])

        report = EvolutionDiagnosisService(self.workspace).diagnose(issue_id)
        bundle = EvidenceBundleBuilder(self.workspace).build(issue_id)
        invalid = replace(bundle, evidence_refs=({"trace_id": "missing", "event_id": "missing"},))
        with self.assertRaises(ValueError):
            validate_evidence_refs(self.workspace, invalid)


if __name__ == "__main__":
    unittest.main()
