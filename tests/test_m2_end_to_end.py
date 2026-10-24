from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import (
    CodexAdapter,
    McpTraceRecorder,
    OpenCodeAdapter,
    StdioObserver,
    TraceIngestor,
)
from shutu_workshop.observers.mcp.common import correlate_events


class M2EndToEndTests(unittest.TestCase):
    def test_codex_opencode_and_mcp_share_one_queryable_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(__file__).resolve().parents[1]
            ingestor = TraceIngestor(Path(temp) / "workspace")
            codex = CodexAdapter(ingestor, workspace_id="e2e")
            codex_text = "\n".join(
                json.dumps(value)
                for value in [
                    {"type": "thread.started", "id": "thread-1", "thread_id": "thread-1"},
                    {"type": "item.started", "id": "call-1", "item": {"type": "mcp_tool_call", "call_id": "3", "name": "echo"}},
                    {"type": "item.completed", "id": "call-1-done", "item": {"type": "mcp_tool_call", "call_id": "3", "name": "echo", "status": "completed"}},
                    {"type": "turn.completed", "id": "turn-1", "turn_id": "turn-1", "status": "completed"},
                ]
            ) + "\n"
            codex_result = codex.ingest_jsonl(codex_text, trace_id="e2e-codex", close=True)
            self.assertEqual(codex_result.accepted, 5)

            opencode = OpenCodeAdapter(ingestor, api_generation="v2", workspace_id="e2e")
            opencode_text = "\n".join(
                json.dumps(value)
                for value in [
                    {"type": "session.created", "id": "session-1", "sessionID": "session-1"},
                    {"type": "tool.execute.before", "id": "call-2", "sessionID": "session-1", "callID": "4", "tool": "echo"},
                    {"type": "tool.execute.after", "id": "call-2-done", "sessionID": "session-1", "callID": "4", "tool": "echo", "status": "completed"},
                ]
            ) + "\n"
            opencode_result = opencode.ingest_jsonl(opencode_text, trace_id="e2e-opencode", close=True)
            self.assertEqual(opencode_result.accepted, 4)

            recorder = McpTraceRecorder(
                ingestor,
                workspace_id="e2e",
                server_name="e2e-fixture",
                adapter_id="mcp-observer-stdio",
                transport="stdio",
            )
            observer = StdioObserver(
                [
                    __import__("sys").executable,
                    "-c",
                    "import sys,json; [sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':3,'result':{'content':[{'type':'text','text':'5'}]}})+'\\n') or sys.stdout.flush() for _ in sys.stdin if _.strip()]",
                ],
                recorder=recorder,
                timeout=10,
            )
            _, report = observer.proxy_bytes(b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"text":"x"}}}\n')
            self.assertEqual(report.process_exit_code, 0)

            events = ingestor.traces.list_events()
            grouped = correlate_events(events)
            request_three = [group for group in grouped if any(event.request_id == "3" for event in group)]
            self.assertEqual(len(request_three), 1)
            self.assertGreaterEqual(len(request_three[0]), 3)
            self.assertEqual({event.request_id for event in request_three[0]}, {"3"})
            self.assertEqual(
                {trace.adapter_id for trace in ingestor.traces.list_traces()},
                {"codex", "opencode", "mcp-observer-stdio"},
            )
            self.assertTrue(all(trace.observation_completeness != "complete" for trace in ingestor.traces.list_traces()))


if __name__ == "__main__":
    unittest.main()
