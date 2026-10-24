from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    CodexAdapter,
    OpenCodeAdapter,
    Trace,
    TraceEvent,
    TraceIngestor,
)


class M2AdapterTests(unittest.TestCase):
    def test_codex_jsonl_is_idempotent_and_keeps_missing_fields_explicit(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "codex_trace.jsonl"
        text = fixture.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temp:
            ingestor = TraceIngestor(Path(temp) / "workspace")
            adapter = CodexAdapter(ingestor, workspace_id="fixture")
            first = adapter.ingest_jsonl(text, trace_id="codex-real-shape", close=True)
            second = adapter.ingest_jsonl(text, trace_id="codex-real-shape")
            self.assertEqual(first.accepted, 7)
            self.assertEqual(second.duplicates, 7)
            trace = ingestor.traces.get("codex-real-shape")
            self.assertEqual(trace.observation_completeness, "partial")
            self.assertEqual(trace.adapter_version, "1")
            events = ingestor.traces.list_events("codex-real-shape")
            self.assertEqual(events[2].tool_name, "search")
            self.assertEqual(events[2].observation_source, "agent_adapter")
            self.assertEqual(adapter.descriptor().field_status["skill_lifecycle"], "not_observed")

    def test_opencode_v1_and_v2_shapes_share_core_fields(self) -> None:
        records = [
            {"type": "session.created", "sessionID": "s1", "id": "session-1"},
            {"type": "tool.execute.before", "sessionID": "s1", "callID": "call-1", "tool": "echo", "input": {"text": "fixture"}},
            {"type": "tool.execute.after", "sessionID": "s1", "callID": "call-1", "tool": "echo", "status": "completed"},
            {"type": "step_finish", "sessionID": "s1", "id": "step-1", "status": "completed"},
        ]
        text = "\n".join(json.dumps(value) for value in records) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            ingestor = TraceIngestor(Path(temp) / "workspace")
            adapter = OpenCodeAdapter(ingestor, api_generation="v2", workspace_id="fixture")
            result = adapter.ingest_jsonl(text, trace_id="opencode-real-shape", close=True)
            self.assertEqual(result.accepted, 5)
            events = ingestor.traces.list_events("opencode-real-shape")
            self.assertEqual([event.type for event in events], ["task.started", "tool.before", "tool.after", "task.finished"])
            self.assertEqual(events[1].request_id, "call-1")
            self.assertEqual(events[1].tool_name, "echo")
            self.assertEqual(events[1].correlation_quality, "exact")

    def test_core_defaults_are_not_optimistic(self) -> None:
        self.assertEqual(Trace().observation_completeness, "unknown")
        self.assertEqual(TraceEvent(trace_id="t", seq=0, type="task.started").correlation_quality, "unknown")


if __name__ == "__main__":
    unittest.main()
