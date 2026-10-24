from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import McpTraceRecorder, StdioObserver, TraceIngestor


class M2MCPStdioTests(unittest.TestCase):
    def test_stdio_forwarding_keeps_jsonrpc_results_and_records_tools(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(repository / "servers"), str(repository / "tests"), environment.get("PYTHONPATH", "")]
        )
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 2, "b": 3}}},
        ]
        payload = b"".join((json.dumps(value, separators=(",", ":")).encode() + b"\n") for value in messages)
        with tempfile.TemporaryDirectory() as temp:
            ingestor = TraceIngestor(Path(temp) / "workspace")
            recorder = McpTraceRecorder(
                ingestor,
                workspace_id="fixture",
                server_name="shutu-fixture",
                adapter_id="mcp-observer-stdio",
                transport="stdio",
            )
            observer = StdioObserver(
                [sys.executable, "-m", "fixtures.mcp_test_server", "--protocol", "legacy"],
                recorder=recorder,
                env=environment,
                timeout=10,
            )
            output, report = observer.proxy_bytes(payload)
            responses = [json.loads(line) for line in output.splitlines() if line.strip()]
            self.assertEqual([value["id"] for value in responses], [1, 2, 3])
            self.assertEqual(responses[-1]["result"]["content"][0]["text"], "5")
            self.assertEqual(report.process_exit_code, 0)
            self.assertEqual(report.failed_observations, 0)
            events = ingestor.traces.list_events(recorder.trace_id)
            self.assertEqual([event.type for event in events if event.type == "mcp.tool_call"], ["mcp.tool_call"])
            result_event = next(event for event in events if event.type == "mcp.tool_result")
            self.assertEqual(result_event.request_id, "3")
            self.assertEqual(result_event.duration_ms is not None, True)
            self.assertEqual(result_event.transport, "stdio")
            with ingestor.db.connection() as connection:
                self.assertGreater(connection.execute("SELECT count(*) FROM capability_versions").fetchone()[0], 0)

    def test_stdio_does_not_replay_when_observation_payload_is_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = Path(__file__).resolve().parents[1]
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join([str(repository / "servers"), str(repository / "tests")])
            observer = StdioObserver(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'not-json\\n'); sys.stdout.flush()"],
                env=environment,
                timeout=10,
            )
            output, report = observer.proxy_bytes(b"{}\n")
            self.assertEqual(output, b"not-json\n")
            self.assertEqual(report.failed_observations, 1)
            self.assertNotIn("replayed", report.errors)


if __name__ == "__main__":
    unittest.main()
