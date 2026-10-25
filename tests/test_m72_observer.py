from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from shutu_workshop import McpTraceRecorder, StdioObserver, TraceIngestor
from shutu_workshop.capture import HookSpoolImporter
from shutu_workshop.observers.mcp.common import correlate_events


REPOSITORY = Path(__file__).resolve().parents[1]
_HOOK_SPEC = importlib.util.spec_from_file_location(
    "m72_ingest_event", REPOSITORY / "plugins" / "shutu-skill-workshop" / "hooks" / "ingest_event.py"
)
assert _HOOK_SPEC and _HOOK_SPEC.loader
ingest_event = importlib.util.module_from_spec(_HOOK_SPEC)
_HOOK_SPEC.loader.exec_module(ingest_event)


class M72ObserverTests(unittest.TestCase):
    def _observer(self, command: list[str], recorder: McpTraceRecorder | None = None, **kwargs: object) -> StdioObserver:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(REPOSITORY / "servers"), str(REPOSITORY / "tests"), environment.get("PYTHONPATH", "")]
        )
        return StdioObserver(command, recorder=recorder, env=environment, **kwargs)

    def test_default_session_is_not_killed_by_a_fixed_lifetime(self) -> None:
        child = textwrap.dedent(
            """
            import json, sys, time
            for line in sys.stdin.buffer:
                if not line.strip():
                    continue
                request = json.loads(line)
                time.sleep(0.12)
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": {"ok": True}}
                sys.stdout.buffer.write((json.dumps(response, separators=(",", ":")) + "\\n").encode())
                sys.stdout.buffer.flush()
            """
        )
        started = time.perf_counter()
        observer = self._observer([sys.executable, "-c", child])
        output, report = observer.proxy_bytes(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"slow"}}\n'
        )
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertEqual(json.loads(output)["id"], 1)
        self.assertFalse(report.timed_out)
        self.assertEqual(report.termination_reason, "child_exit")

    def test_startup_timeout_terminates_a_server_without_a_response(self) -> None:
        child = "import time; time.sleep(1)"
        output, report = self._observer(
            [sys.executable, "-c", child], startup_timeout=0.05, shutdown_timeout=0.2
        ).proxy_bytes(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        self.assertEqual(output, b"")
        self.assertTrue(report.timed_out)
        self.assertEqual(report.termination_reason, "startup_timeout")
        self.assertEqual(report.process_exit_code is None, False)

    def test_content_length_framing_is_forwarded_without_rewriting(self) -> None:
        child = textwrap.dedent(
            """
            import json, sys
            headers = b""
            while not headers.endswith(b"\\r\\n\\r\\n"):
                part = sys.stdin.buffer.readline()
                if not part:
                    raise SystemExit(1)
                headers += part
            length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\\r\\n") if line.lower().startswith(b"content-length:")))
            request = json.loads(sys.stdin.buffer.read(length))
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}, separators=(",", ":")).encode()
            sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\\r\\n\\r\\n" + body)
            sys.stdout.buffer.flush()
            """
        )
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}, separators=(",", ":")).encode()
        payload = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        output, report = self._observer([sys.executable, "-c", child]).proxy_bytes(payload)
        expected_body = b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}'
        self.assertEqual(output, b"Content-Length: " + str(len(expected_body)).encode() + b"\r\n\r\n" + expected_body)
        self.assertEqual(report.failed_observations, 0, report.to_dict())

    def test_same_id_is_scoped_to_connection_and_server_requests_have_direction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ingestor = TraceIngestor(Path(temporary) / "workspace")
            recorder = McpTraceRecorder(ingestor, server_name="fixture", transport="stdio")
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "one"}}, connection_id="c1")
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "two"}}, connection_id="c2")
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}}, connection_id="c2")
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "result": {}}, connection_id="c1")
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, connection_id="c1")
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "result": {}}, connection_id="c1")
            recorder.close(status="ok")
            events = ingestor.traces.list_events(recorder.trace_id)
            calls = [event for event in events if event.type == "mcp.tool_call"]
            results = [event for event in events if event.type == "mcp.tool_result"]
            self.assertEqual(len(calls), 2)
            self.assertEqual({event.source_metadata["correlation_scope"]["mcp_connection"] for event in results}, {"c1", "c2"})
            groups = correlate_events(calls + results)
            self.assertEqual(sorted(len(group) for group in groups), [2, 2])
            server_request = next(event for event in events if event.type == "mcp.tools_list")
            self.assertEqual(server_request.source_metadata["correlation_scope"]["mcp_connection"], "c1")
            self.assertEqual(recorder.report.duplicate_requests, 0)

    def test_protocol_business_and_cancel_states_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ingestor = TraceIngestor(Path(temporary) / "workspace")
            recorder = McpTraceRecorder(ingestor, server_name="fixture", transport="stdio")
            for request_id in (1, 2, 3):
                recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": "x"}})
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}})
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "invalid"}})
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": 3}})
            recorder.close(status="ok")
            events = ingestor.traces.list_events(recorder.trace_id)
            business = next(event for event in events if event.type == "mcp.tool_result" and event.request_id == "1")
            protocol = next(event for event in events if event.type == "mcp.tool_result" and event.request_id == "2")
            cancelled = next(event for event in events if event.type == "mcp.cancel")
            self.assertEqual(business.error_type, "mcp.error")
            self.assertEqual(protocol.error_type, "mcp.protocol_error")
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(recorder.report.notification_count, 1)
            self.assertEqual(recorder.report.failed_observations, 0, recorder.report.to_dict())

    def test_persisted_mcp_business_error_reaches_existing_issue_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            ingestor = TraceIngestor(Path(temporary) / "workspace")
            recorder = McpTraceRecorder(ingestor, server_name="fixture", transport="stdio")
            recorder.observe_json("client_to_server", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "error"}})
            recorder.observe_json("server_to_client", {"jsonrpc": "2.0", "id": 1, "result": {"isError": True}})
            from shutu_workshop.mcp_server.service import WorkshopMcpService

            live_listing = WorkshopMcpService(Path(temporary) / "workspace").list_issues()
            self.assertEqual(live_listing["count"], 1)
            recorder.close(status="ok")
            from shutu_workshop.diagnostics import DiagnosisService

            issues = DiagnosisService(Path(temporary) / "workspace").list_issues()
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].rule_id, "R01")
            self.assertEqual(issues[0].occurrence_count, 1)

    def test_hook_missing_identity_is_unique_unknown_and_not_success(self) -> None:
        first, first_unknown = ingest_event._summary({"hook_event_name": "PostToolUse", "tool_name": "workshop_health"})
        second, second_unknown = ingest_event._summary({"hook_event_name": "PostToolUse", "tool_name": "workshop_health"})
        self.assertTrue(first_unknown and second_unknown)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["event_id_quality"], "generated/unknown")
        self.assertEqual(first["result_status"], "unknown")

        with tempfile.TemporaryDirectory() as temporary:
            plugin_data = Path(temporary) / "plugin-data"
            plugin_data.mkdir()
            (plugin_data / "hook_events.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
            importer = HookSpoolImporter(Path(temporary) / "workspace", plugin_data)
            imported = importer.flush()
            self.assertEqual(imported.accepted, 2)
            events = importer.ingestor.traces.list_events()
            self.assertEqual(len(events), 2)
            self.assertEqual({event.status for event in events}, {"unknown"})
            self.assertEqual({event.correlation_quality for event in events}, {"unknown"})

    def test_m72_scripts_whatif_and_recursion_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = subprocess.run(
                ["powershell", "-NoProfile", "-File", str(REPOSITORY / "scripts" / "install-m72-observer.ps1"), "-CodexHome", temporary, "-WhatIf"],
                cwd=REPOSITORY, text=True, capture_output=True, timeout=30, check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertEqual(json.loads(install.stdout)["status"], "WHAT_IF")
            self.assertFalse((Path(temporary) / "config.toml").exists())
        recursion = subprocess.run(
            [sys.executable, str(REPOSITORY / "scripts" / "m72_stdio_observer.py"), "--", sys.executable, str(REPOSITORY / "scripts" / "m72_stdio_observer.py")],
            cwd=REPOSITORY, text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(recursion.returncode, 2)


if __name__ == "__main__":
    unittest.main()
