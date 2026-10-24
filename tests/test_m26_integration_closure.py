from __future__ import annotations

import concurrent.futures
import http.client
import json
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from shutu_workshop import (
    CodexAdapter,
    McpTraceRecorder,
    OpenCodeAdapter,
    TraceEvent,
    TraceIngestor,
    create_relay_server,
)
from shutu_workshop.adapters.opencode.event_sink import create_event_sink_server, serve_in_thread as serve_sink
from shutu_workshop.observers.mcp.common import correlate_events
from shutu_workshop.observers.mcp.relay import serve_in_thread as serve_relay
from tests.fixtures.mcp_test_server import start_http_server


def _http_json(port: int, path: str, body: dict | None = None, *, token: str = "") -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    encoded = json.dumps(body or {}, separators=(",", ":")).encode("utf-8") if body is not None else b""
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("POST" if body is not None else "GET", path, body=encoded or None, headers=headers)
    response = connection.getresponse()
    value = json.loads(response.read().decode("utf-8"))
    status = response.status
    connection.close()
    return status, value


def _relay_call(port: int, value: dict, *, token: str = "relay-token") -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    connection.request(
        "POST",
        "/mcp",
        body=body,
        headers={
            "Content-Type": "application/json",
            "X-Shutu-Workshop-Relay-Token": token,
        },
    )
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


class M26IntegrationClosureTests(unittest.TestCase):
    def test_opencode_sink_is_authenticated_bounded_and_incremental(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            ingestor = TraceIngestor(Path(temp) / "workspace")
            server = create_event_sink_server(ingestor, token="sink-token")
            thread = serve_sink(server)
            try:
                status, _ = _http_json(server.server_port, "/events", {"type": "tool.before"})
                self.assertEqual(status, 401)
                events = [
                    {"type": "observer.ready", "plugin_id": "fixture", "host_version": "v2"},
                    {"type": "tool.before", "sessionID": "session-1", "callID": "call-1", "tool": "echo"},
                    {"type": "tool.after", "sessionID": "session-1", "callID": "call-1", "tool": "echo", "status": "completed"},
                ]
                for event in events:
                    status, result = _http_json(server.server_port, "/events", event, token="sink-token")
                    self.assertEqual(status, 200)
                    self.assertNotIn("error", result)
                loaded = ingestor.traces.list_events()
                self.assertEqual([event.type for event in loaded], ["observer.ready", "tool.before", "tool.after"])
                self.assertEqual(loaded[1].request_id, "call-1")
                raw = json.dumps([event.to_dict() for event in loaded], ensure_ascii=False)
                self.assertNotIn("input", raw)
                self.assertNotIn("secret", raw)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_correlation_scope_blocks_bare_cross_source_join_and_allows_manifest_bridge(self) -> None:
        def event(source: str, connection: str, event_id: str) -> TraceEvent:
            return TraceEvent(
                trace_id=f"trace-{source}",
                seq=0,
                type="mcp.tool_call" if source == "mcp_observer" else "tool.before",
                event_id=event_id,
                request_id="1",
                tool_name="echo",
                observation_source=source,
                transport="stdio" if source == "mcp_observer" else "codex-cli",
                source_metadata={
                    "correlation_scope": {
                        "workspace_id": "w",
                        "run_id": f"run-{source}",
                        "agent_session_id": "agent-1" if source == "agent_adapter" else "",
                        "mcp_server": "fixture" if source == "mcp_observer" else "",
                        "mcp_connection": connection if source == "mcp_observer" else "",
                        "transport": "stdio" if source == "mcp_observer" else "codex-cli",
                        "request_id": "1",
                    }
                },
            )

        codex = event("agent_adapter", "", "agent-1")
        mcp_a = event("mcp_observer", "connection-a", "mcp-a")
        mcp_b = event("mcp_observer", "connection-b", "mcp-b")
        groups = correlate_events([codex, mcp_a, mcp_b])
        self.assertEqual(sorted(len(group) for group in groups), [1, 1, 1])
        self.assertEqual(sum(1 for group in groups if len(group) > 1), 0)

        bridge = {"bridge_id": "manifest-1", "verified": True, "source": "run-manifest"}
        linked = [
            replace(codex, source_metadata={**codex.source_metadata, "correlation_evidence": bridge}),
            replace(mcp_a, source_metadata={**mcp_a.source_metadata, "correlation_evidence": bridge}),
        ]
        self.assertEqual(len(correlate_events(linked)), 1)

    def test_http_relay_is_application_transparent_and_scopes_concurrent_ids(self) -> None:
        upstream, upstream_thread = start_http_server(protocol="legacy")
        try:
            with tempfile.TemporaryDirectory() as temp:
                ingestor = TraceIngestor(Path(temp) / "workspace")
                recorder = McpTraceRecorder(
                    ingestor,
                    workspace_id="relay-test",
                    server_name="shutu-fixture",
                    adapter_id="mcp-observer-relay",
                    transport="streamable-http-relay",
                )
                relay = create_relay_server(
                    f"http://127.0.0.1:{upstream.server_port}/mcp",
                    recorder=recorder,
                    listener_token="relay-token",
                )
                relay_thread = serve_relay(relay)
                try:
                    status, body = _relay_call(
                        relay.server_port,
                        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "ok"}}},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["result"]["content"][0]["text"], "ok")
                    status, body = _relay_call(
                        relay.server_port,
                        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "error", "arguments": {}}},
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(json.loads(body)["result"]["isError"])

                    def call(text: str) -> bytes:
                        _, result = _relay_call(
                            relay.server_port,
                            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": text}}},
                        )
                        return result

                    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(call, ["a", "b"]))
                    self.assertEqual({json.loads(value)["result"]["content"][0]["text"] for value in results}, {"a", "b"})
                    deadline = time.time() + 2
                    while time.time() < deadline:
                        if len([event for event in ingestor.traces.list_events(recorder.trace_id) if event.request_id == "1"]) >= 6:
                            break
                        time.sleep(0.01)
                    events = ingestor.traces.list_events(recorder.trace_id)
                    id_one = [event for event in events if event.request_id == "1"]
                    self.assertGreaterEqual(len(id_one), 6)
                    groups = correlate_events(id_one)
                    self.assertEqual(sorted(len(group) for group in groups), [2, 2, 2])
                    self.assertEqual(recorder.report.failed_observations, 0)
                finally:
                    recorder.close()
                    relay.shutdown()
                    relay.server_close()
                    relay_thread.join(5)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(5)

    def test_stream_capture_bounds_output_marks_partial_lines_and_deduplicates_restart(self) -> None:
        records = [
            {"type": "thread.started", "id": "thread-1", "thread_id": "thread-1"},
            {"type": "turn.completed", "id": "turn-1", "turn_id": "turn-1", "status": "completed"},
        ] + [
            {"type": "item.completed", "id": f"item-{index}", "item": {"type": "agent_message"}}
            for index in range(10)
        ]
        output = "\n".join(json.dumps(record, separators=(",", ":")) for record in records) + "\n"
        script = f"import sys; sys.stdout.write({output!r}); sys.stderr.write('x'*100000)"
        with tempfile.TemporaryDirectory() as temp:
            adapter = CodexAdapter(TraceIngestor(Path(temp) / "workspace"), workspace_id="capture")
            first = adapter.capture_stream(
                [sys.executable, "-c", script],
                trace_id="capture-run",
                batch_size=1,
                max_stdout_bytes=256,
                max_stderr_bytes=256,
            )
            second = adapter.capture_stream(
                [sys.executable, "-c", script],
                trace_id="capture-run",
                batch_size=1,
                max_stdout_bytes=256,
                max_stderr_bytes=256,
            )
            self.assertEqual(first.exit_code, 0)
            self.assertTrue(first.output_truncated)
            self.assertGreater(first.accepted, 0)
            self.assertEqual(second.accepted, 0)
            self.assertEqual(len(adapter.ingestor.traces.list_events("capture-run")), len(records))

            partial_script = "import sys; sys.stdout.write('{\\\"type\\\":\\\"thread.started\\\"}')"
            partial = adapter.capture_stream([sys.executable, "-c", partial_script], trace_id="partial-run")
            self.assertEqual(partial.incomplete_lines, 1)
            self.assertIn("partial_line", partial.errors)
            self.assertEqual(adapter.ingestor.traces.list_events("partial-run"), [])


if __name__ == "__main__":
    unittest.main()
