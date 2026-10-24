from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import McpTraceRecorder, StreamableHTTPObserver, TraceIngestor
from tests.fixtures.mcp_test_server import start_http_server


class M2MCPHTTPTests(unittest.TestCase):
    def test_loopback_http_keeps_json_business_result_and_redacts_headers(self) -> None:
        server, thread = start_http_server(protocol="legacy")
        try:
            with tempfile.TemporaryDirectory() as temp:
                ingestor = TraceIngestor(Path(temp) / "workspace")
                recorder = McpTraceRecorder(
                    ingestor,
                    workspace_id="fixture",
                    server_name="shutu-fixture",
                    adapter_id="mcp-observer-http",
                    transport="streamable-http",
                )
                observer = StreamableHTTPObserver(
                    f"http://127.0.0.1:{server.server_port}/mcp",
                    recorder=recorder,
                    timeout=10,
                )
                initialize = observer.request(
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode(),
                    headers={"Content-Type": "application/json", "Authorization": "Bearer fixture-secret"},
                )
                listed = observer.request(
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                called = observer.request(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "echo", "arguments": {"text": "secret fixture"}},
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                report = observer.close()
                self.assertEqual(initialize.status, 200)
                self.assertEqual(json.loads(called.body)["result"]["content"][0]["text"], "secret fixture")
                self.assertGreater(len(json.loads(listed.body)["result"]["tools"]), 1)
                self.assertEqual(report.response_count, 3)
                events = ingestor.traces.list_events(recorder.trace_id)
                self.assertTrue(any(event.type == "mcp.tool_call" for event in events))
                raw = json.dumps([event.to_dict() for event in events], ensure_ascii=False)
                self.assertNotIn("fixture-secret", raw)
                self.assertNotIn("secret fixture", raw)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_modern_discover_and_sse_are_application_transparent(self) -> None:
        server, thread = start_http_server(protocol="modern", sse=True)
        try:
            recorder = McpTraceRecorder(transport="streamable-http", server_name="shutu-fixture")
            observer = StreamableHTTPObserver(
                f"http://127.0.0.1:{server.server_port}/mcp",
                recorder=recorder,
                timeout=10,
            )
            result = observer.request(
                b'{"jsonrpc":"2.0","id":7,"method":"server/discover","params":{}}',
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                    "Mcp-Name": "discover",
                },
            )
            report = observer.close()
            self.assertEqual(result.status, 200)
            self.assertIn(b"data:", result.body)
            self.assertEqual(report.protocol_era, "modern")
            self.assertEqual(report.errors, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_external_http_target_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StreamableHTTPObserver("https://example.com/mcp")


if __name__ == "__main__":
    unittest.main()
