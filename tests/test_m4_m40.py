import tempfile
import unittest
from pathlib import Path

from shutu_workshop import DiagnosisService, Trace, TraceEvent, TraceIngestor
from shutu_workshop.diagnostics import DetectorContext, RuleDetector, RuleRegistry, RuleSpec


def _event(trace_id: str, seq: int, *, event_type: str = "mcp.tool_result", **kwargs) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        seq=seq,
        type=event_type,
        actor="mcp",
        event_id=f"{trace_id}-event-{seq}",
        status=kwargs.pop("status", "error"),
        error_type=kwargs.pop("error_type", ""),
        transport=kwargs.pop("transport", "relay"),
        payload_json=kwargs.pop("payload", {"result": {"isError": True}}),
        source_metadata=kwargs.pop("metadata", {}),
        **kwargs,
    )


class _FailOnceDetector(RuleDetector):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def detect(self, context: DetectorContext):
        if not self.failed:
            self.failed = True
            raise RuntimeError("simulated interruption")
        return super().detect(context)


class M40SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.ingestor = TraceIngestor(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_bounded_scheduler_reaches_tail_and_reconciles_late_event(self) -> None:
        for index in range(1, 306):
            trace_id = f"trace-{index:03d}"
            self.ingestor.start(Trace(trace_id=trace_id, workspace_id="w", adapter_id="mcp", status="failed"))
            self.ingestor.append(_event(trace_id, 0))

        service = DiagnosisService(self.workspace)
        batches = [service.analyze_new(limit=25) for _ in range(13)]
        self.assertEqual(sum(item.analyzed_count for item in batches), 305)
        self.assertEqual(batches[0].eligible, 25)
        self.assertTrue(all(item.remaining_or_unknown == "unknown" for item in batches))
        with service.db.connection() as connection:
            checkpoint = connection.execute(
                "SELECT status FROM analysis_checkpoints WHERE trace_id = ?",
                ("trace-300",),
            ).fetchone()
        self.assertEqual(checkpoint[0], "completed")

        self.ingestor.append(_event("trace-001", 1))
        late = service.analyze_new(limit=25)
        self.assertGreaterEqual(late.analyzed_count, 1)
        with service.db.connection() as connection:
            before_replay = connection.execute(
                "SELECT COUNT(*) FROM issue_occurrences WHERE trace_id = ?",
                ("trace-001",),
            ).fetchone()[0]
        self.assertEqual(before_replay, 2)
        service.analyze_new(limit=25)
        with service.db.connection() as connection:
            after_replay = connection.execute(
                "SELECT COUNT(*) FROM issue_occurrences WHERE trace_id = ?",
                ("trace-001",),
            ).fetchone()[0]
        self.assertEqual(after_replay, before_replay)

    def test_interrupted_trace_has_no_completed_checkpoint_and_recovers(self) -> None:
        self.ingestor.start(Trace(trace_id="interrupted", workspace_id="w", adapter_id="mcp", status="failed"))
        self.ingestor.append(_event("interrupted", 0))
        service = DiagnosisService(self.workspace, detector=_FailOnceDetector())
        first = service.analyze_new(limit=1)
        self.assertEqual(first.status, "failed")
        with service.db.connection() as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM analysis_checkpoints WHERE trace_id = ?",
                ("interrupted",),
            ).fetchone())
        second = service.analyze_new(limit=1)
        self.assertEqual(second.analyzed_count, 1)


class M40RulesetAndAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.ingestor = TraceIngestor(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_rulesets_are_isolated_and_r02_keeps_unknown_boundary_unknown(self) -> None:
        self.ingestor.start(Trace(trace_id="same", workspace_id="w", adapter_id="mcp", status="failed"))
        self.ingestor.append(_event("same", 0))
        service = DiagnosisService(self.workspace)
        ruleset_a = [RuleSpec("R01", "a", severity_policy={"default": "error"})]
        ruleset_b = [RuleSpec("R01", "b", severity_policy={"default": "error"})]
        service.analyze_trace("same", ruleset=ruleset_a)
        service.analyze_trace("same", ruleset=ruleset_b)
        with service.db.connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT ruleset_fingerprint FROM issue_occurrences WHERE trace_id = ?",
                ("same",),
            ).fetchall()
            history_count = connection.execute("SELECT COUNT(*) FROM issue_status_history").fetchone()[0]
        self.assertEqual(len(rows), 2)
        self.assertEqual(history_count, 0)

        trace = Trace(trace_id="attribution", workspace_id="w", adapter_id="mcp", status="failed")
        events = (
            _event("attribution", 0, event_type="mcp.transport_error", error_type="http.401", payload={}),
            _event("attribution", 1, event_type="mcp.transport_error", error_type="http.403", payload={}),
            _event("attribution", 2, event_type="mcp.transport_error", error_type="timeout", payload={}),
            _event("attribution", 3, event_type="mcp.transport_error", error_type="http.502", payload={}),
            _event("attribution", 4, event_type="mcp.transport_error", error_type="sqlite.error", payload={}, metadata={"failure_layer": "observer"}),
            _event("attribution", 5, payload={"result": {"isError": True}, "error": {"code": "BUSINESS"}}),
        )
        findings = RuleDetector().detect(DetectorContext(trace, events))
        r02 = [item for item in findings if item.rule_id == "R02"]
        self.assertEqual([item.category for item in r02], [
            "auth_failure_observed",
            "auth_failure_observed",
            "network_failure_observed",
            "upstream_boundary_failure_observed",
            "observer_failure_observed",
        ])
        self.assertEqual(r02[3].owner_candidates[0].owner_kind, "unknown")
        self.assertEqual(sum(item.rule_id == "R01" for item in findings), 1)


if __name__ == "__main__":
    unittest.main()
