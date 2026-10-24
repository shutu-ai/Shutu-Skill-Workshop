from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    DiagnosisService,
    Trace,
    TraceEvent,
    TraceIngestor,
)
from shutu_workshop.diagnostics import (
    ConcurrentUpdateError,
    DetectorContext,
    RuleDetector,
)
from shutu_workshop.observers.mcp.common import CorrelationScope, correlate_events


def event(
    trace_id: str,
    seq: int,
    event_type: str,
    *,
    event_id: str | None = None,
    source: str = "mcp_observer",
    request_id: str = "1",
    tool_name: str = "echo",
    status: str = "",
    error_type: str = "",
    payload: dict | None = None,
    metadata: dict | None = None,
    capability_id: str = "",
    capability_version_id: str = "",
    retry_of_event_id: str = "",
    attempt_index: int | None = None,
) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        seq=seq,
        event_id=event_id or f"event-{trace_id}-{seq}",
        type=event_type,
        actor="tool",
        observation_source=source,
        transport="streamable-http" if source == "mcp_observer" else "codex-cli",
        request_id=request_id,
        tool_name=tool_name,
        status=status,
        error_type=error_type,
        capability_id=capability_id,
        capability_version_id=capability_version_id,
        retry_of_event_id=retry_of_event_id,
        attempt_index=attempt_index,
        payload_json=payload or {},
        source_metadata=metadata or {
            "correlation_scope": {
                "workspace_id": "test",
                "run_id": trace_id,
                "mcp_server": "fixture",
                "mcp_connection": "connection-a",
                "transport": "streamable-http",
            }
        },
    )


class M3RuleTests(unittest.TestCase):
    def test_r01_success_is_not_a_failure_and_iserror_is(self) -> None:
        trace = Trace(trace_id="t", workspace_id="w", adapter_id="mcp", status="ok")
        detector = RuleDetector()
        success = event("t", 0, "mcp.tool_result", status="ok", payload={"result": {"isError": False}})
        failure = event("t", 1, "mcp.tool_result", status="error", payload={"result": {"isError": True}})
        findings = detector.detect(DetectorContext(trace, (success, failure)))
        self.assertEqual([item.rule_id for item in findings], ["R01"])
        self.assertEqual(findings[0].category, "tool_failure_observed")

    def test_r02_separates_auth_network_and_observer_failures(self) -> None:
        trace = Trace(trace_id="t", workspace_id="w", adapter_id="mcp", status="failed")
        events = (
            event("t", 0, "mcp.transport_error", error_type="http.401", status="error"),
            event("t", 1, "mcp.transport_error", error_type="timeout", status="error"),
            event("t", 2, "mcp.transport_error", error_type="http.502", status="error", metadata={"layer": "relay"}),
        )
        findings = RuleDetector().detect(DetectorContext(trace, events))
        self.assertEqual([item.category for item in findings], [
            "auth_failure_observed",
            "network_failure_observed",
            "observer_failure_observed",
        ])
        self.assertEqual([item.owner_candidates[0].owner_kind for item in findings], ["auth", "network", "observer"])

    def test_r03_requires_schema_and_validation_evidence(self) -> None:
        trace = Trace(trace_id="t", workspace_id="w", adapter_id="mcp", status="failed")
        only_keys = event("t", 0, "mcp.tool_call", payload={"argument_keys": ["text"]})
        explicit = event(
            "t",
            1,
            "mcp.tool_result",
            payload={
                "schema_validation": {
                    "schema_hash": "schema-1",
                    "schema_version": "v1",
                    "valid": False,
                    "violations": [{"path": "/text", "type": "string"}],
                }
            },
        )
        findings = RuleDetector().detect(DetectorContext(trace, (only_keys, explicit)))
        self.assertEqual([item.rule_id for item in findings], ["R03", "R03"])
        self.assertEqual(findings[0].attribution_state, "insufficient_evidence")
        self.assertEqual(findings[1].category, "schema_mismatch_observed")
        self.assertNotIn("string", str(findings[1].to_dict()))

    def test_r04_needs_explicit_retry_chain_and_r05_does_not_fail_running_trace(self) -> None:
        running = Trace(trace_id="running", workspace_id="w", adapter_id="mcp", status="running", observation_completeness="partial")
        started = event("running", 0, "mcp.tool_call", event_id="call", status="started")
        self.assertEqual(RuleDetector().detect(DetectorContext(running, (started,))), [])

        trace = Trace(trace_id="retry", workspace_id="w", adapter_id="mcp", status="failed", observation_completeness="partial")
        first = event("retry", 0, "mcp.tool_result", event_id="first", status="error", error_type="timeout")
        second = event("retry", 1, "mcp.tool_result", event_id="second", status="error", error_type="timeout", retry_of_event_id="first", attempt_index=2)
        findings = RuleDetector().detect(DetectorContext(trace, (first, second)))
        self.assertEqual([item.rule_id for item in findings], ["R02", "R02", "R04", "R05"])
        chained = (
            event("retry", 2, "mcp.tool_result", event_id="third", status="error", error_type="timeout", attempt_index=1),
            event("retry", 3, "mcp.tool_result", event_id="fourth", status="error", error_type="timeout", attempt_index=2),
        )
        self.assertEqual(sum(item.rule_id == "R04" for item in RuleDetector().detect(DetectorContext(trace, chained))), 1)

    def test_r06_is_not_assessable_without_explicit_expectation(self) -> None:
        trace = Trace(trace_id="t", workspace_id="w", adapter_id="agent", status="ok")
        self.assertEqual(RuleDetector().detect(DetectorContext(trace, (event("t", 0, "task.finished", source="agent_adapter"),))), [])
        expected = Trace(
            trace_id="expected",
            workspace_id="w",
            adapter_id="agent",
            status="ok",
            metadata={
                "expected_capability_id": "skill:expected",
                "expected_capability_discoverable": True,
                "trigger_conditions_met": True,
                "observation_scope_sufficient": True,
            },
        )
        findings = RuleDetector().detect(DetectorContext(expected, (event("expected", 0, "task.finished", source="agent_adapter"),)))
        self.assertEqual([item.category for item in findings], ["skill_missing_trigger"])
        incomplete = Trace(
            trace_id="incomplete-expectation",
            workspace_id="w",
            adapter_id="agent",
            status="ok",
            metadata={"expected_capability_id": "skill:expected"},
        )
        findings = RuleDetector().detect(
            DetectorContext(incomplete, (event("incomplete-expectation", 0, "task.finished", source="agent_adapter"),))
        )
        self.assertEqual([item.category for item in findings], ["not_assessable"])
        self.assertEqual(findings[0].attribution_state, "insufficient_evidence")


class M3LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.ingestor = TraceIngestor(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_trace(self, trace_id: str, *, status: str = "failed", event_id: str | None = None) -> None:
        self.ingestor.start(Trace(trace_id=trace_id, workspace_id="w", adapter_id="mcp", status=status, observation_completeness="complete"))
        self.ingestor.append(
            event(
                trace_id,
                0,
                "mcp.tool_result",
                event_id=event_id,
                status="error",
                payload={"result": {"isError": True}},
            )
        )

    def test_issue_cluster_occurrence_replay_status_and_reopen(self) -> None:
        self.add_trace("one")
        self.add_trace("two")
        service = DiagnosisService(self.workspace)
        first = service.analyze_new(limit=100)
        self.assertEqual(first.occurrence_count, 2)
        issue = service.list_issues()[0]
        self.assertEqual(issue.occurrence_count, 2)
        replay = service.analyze_new(limit=100)
        self.assertEqual(replay.skipped_count, 2)
        self.assertEqual(service.list_issues()[0].occurrence_count, 2)

        acknowledged = service.set_issue_status(issue.issue_id, "acknowledged", expected_row_version=issue.row_version)
        self.assertEqual(acknowledged.status, "acknowledged")
        with self.assertRaises(ConcurrentUpdateError):
            service.set_issue_status(issue.issue_id, "resolved", expected_row_version=issue.row_version)
        resolved = service.set_issue_status(issue.issue_id, "resolved", expected_row_version=acknowledged.row_version)
        self.assertEqual(resolved.status, "resolved")

        self.add_trace("three")
        service.analyze_trace("three")
        reopened = service.list_issues()[0]
        self.assertEqual(reopened.status, "open")
        self.assertEqual(reopened.occurrence_count, 3)
        details = service.show_issue(reopened.issue_id)
        self.assertIsNotNone(details)
        self.assertEqual(len(details.occurrences), 3)

    def test_late_event_changes_revision_and_privacy_is_preserved(self) -> None:
        self.ingestor.start(Trace(trace_id="late", workspace_id="w", adapter_id="agent", status="ok", observation_completeness="complete"))
        self.ingestor.append(event("late", 0, "task.finished", source="agent_adapter", request_id="", payload={"prompt": "password=secret-token"}))
        service = DiagnosisService(self.workspace)
        first = service.analyze_trace("late")
        self.assertEqual(first.occurrence_count, 0)
        self.ingestor.append(event("late", 1, "mcp.tool_result", status="error", payload={"result": {"isError": True}, "secret": "token-value"}))
        second = service.analyze_trace("late")
        self.assertEqual(second.occurrence_count, 1)
        raw = str(service.show_issue(service.list_issues()[0].issue_id).to_dict())
        self.assertNotIn("secret-token", raw)
        self.assertNotIn("token-value", raw)

    def test_migration_and_scoped_ids_are_additive(self) -> None:
        with self.ingestor.db.connection() as connection:
            versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(versions, [1, 2, 3, 4])
        self.assertTrue({"issues", "release_operations", "analysis_runs", "issue_clusters", "issue_occurrences"}.issubset(tables))
        scope_a = CorrelationScope(workspace_id="w", run_id="r", mcp_server="a", mcp_connection="a", transport="stdio", request_id="3").to_dict()
        scope_b = CorrelationScope(workspace_id="w", run_id="r", mcp_server="b", mcp_connection="b", transport="stdio", request_id="3").to_dict()
        a = event("a", 0, "mcp.tool_call", metadata={"correlation_scope": scope_a})
        b = event("b", 0, "mcp.tool_call", metadata={"correlation_scope": scope_b})
        self.assertEqual(sum(len(group) > 1 for group in correlate_events([a, b])), 0)


if __name__ == "__main__":
    unittest.main()
