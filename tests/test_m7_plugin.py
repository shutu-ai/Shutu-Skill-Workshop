from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import Trace, TraceEvent, TraceIngestor


REPOSITORY = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY / "plugins" / "shutu-skill-workshop"


class M7PluginTests(unittest.TestCase):
    def _server(self, workspace: Path, requests: list[dict]) -> list[dict]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(REPOSITORY / "servers")
        payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in requests) + "\n"
        result = subprocess.run(
            [sys.executable, "-m", "shutu_workshop.mcp_server.server", "--workspace", str(workspace)],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=REPOSITORY,
            env=environment,
            check=True,
            timeout=10,
        )
        self.assertEqual(result.stderr, b"")
        return [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]

    def test_package_manifest_and_marketplace_use_portable_paths(self) -> None:
        manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))
        marketplace = json.loads((REPOSITORY / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "shutu-skill-workshop")
        self.assertEqual(manifest["extensions"]["com.openai"]["hooks"], "./hooks/hooks.json")
        self.assertTrue(manifest["extensions"]["com.openai"]["requires_local_executor"])
        entry = next(item for item in marketplace["plugins"] if item["name"] == manifest["name"])
        self.assertEqual(entry["source"]["path"], "./plugins/shutu-skill-workshop")
        self.assertEqual(json.loads((PLUGIN_ROOT / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["shutu-workshop"]["type"], "stdio")

    def test_mcp_initialize_list_and_real_core_queries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m7 space ") as temporary:
            workspace = Path(temporary) / "中文 workspace"
            ingestor = TraceIngestor(workspace)
            ingestor.start(Trace(trace_id="m7-trace", workspace_id="m7", adapter_id="test", source="test"))
            ingestor.append(
                TraceEvent(
                    trace_id="m7-trace",
                    seq=0,
                    event_id="m7-event",
                    type="tool.result",
                    actor="tool",
                    status="error",
                    error_type="fixture.error",
                    payload_json={"secret": "must-not-leak"},
                )
            )
            responses = self._server(
                workspace,
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "workshop_health", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "workshop_recent_activity", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "workshop_list_issues", "arguments": {}}},
                ],
            )
            self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "shutu-skill-workshop")
            self.assertEqual(len(responses[1]["result"]["tools"]), 7)
            health = json.loads(responses[2]["result"]["content"][0]["text"])
            activity = json.loads(responses[3]["result"]["content"][0]["text"])
            issues = json.loads(responses[4]["result"]["content"][0]["text"])
            self.assertEqual(health["database"]["counts"]["trace_events"], 1)
            self.assertEqual(activity["activities"][0]["event_id"], "m7-event")
            self.assertNotIn("must-not-leak", responses[3]["result"]["content"][0]["text"])
            self.assertEqual(issues["data_status"], "NO_ACTIONABLE_ISSUE")

    def test_mcp_invalid_tool_call_is_error_without_host_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            responses = self._server(
                Path(temporary),
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "workshop_issue_detail", "arguments": {}}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "not-a-tool", "arguments": {}}},
                ],
            )
            self.assertTrue(responses[0]["result"]["isError"])
            self.assertTrue(responses[1]["result"]["isError"])

    def test_hook_is_fail_open_bounded_and_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "plugin data with space"
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = str(data)
            environment["SHUTU_WORKSHOP_HOOK_RETENTION"] = "2"
            command = [sys.executable, str(PLUGIN_ROOT / "hooks" / "ingest_event.py")]
            for index in range(3):
                result = subprocess.run(
                    command,
                    input=json.dumps(
                        {
                            "hook_event_name": "PostToolUse",
                            "session_id": "session-secret",
                            "tool_name": "Bash",
                            "tool_input": {"command": "echo secret-command", "password": "secret-password"},
                            "tool_response": {"isError": False, "result": "secret-result"},
                        }
                    ).encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    check=True,
                )
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
            events = (data / "hook_events.jsonl").read_text(encoding="utf-8")
            state = json.loads((data / "hook_state.json").read_text(encoding="utf-8"))
            self.assertLessEqual(len(events.splitlines()), 2)
            self.assertGreaterEqual(state["dropped"], 1)
            self.assertNotIn("secret-command", events)
            self.assertNotIn("secret-password", events)
            self.assertNotIn("secret-result", events)


if __name__ == "__main__":
    unittest.main()
