from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import DiagnosisService, TraceIngestor
from shutu_workshop.capture import HookSpoolImporter
from shutu_workshop.mcp_server.service import WorkshopMcpService


class M71HookCoreTests(unittest.TestCase):
    def _event(self, event_id: str, *, status: str = "ok", tool_name: str = "workshop_health") -> dict[str, object]:
        return {
            "event_id": event_id,
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "session_id_hash": "session-1",
            "request_id_hash": f"request-{event_id}",
            "cwd_hash": "cwd-1",
            "result_status": status,
            "argument_keys": ["limit"],
            "captured_at": f"2026-10-25T00:00:0{event_id[-1]}+00:00",
            "redaction": "summary_only",
        }

    def test_spool_import_is_idempotent_and_survives_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            plugin_data = root / "plugin data"
            spool = plugin_data / "hook_events.jsonl"
            plugin_data.mkdir()
            spool.write_text(
                "\n".join(json.dumps(self._event(f"event-{index}")) for index in (1, 2)) + "\n",
                encoding="utf-8",
            )
            importer = HookSpoolImporter(workspace, plugin_data)
            first = importer.flush()
            self.assertEqual(first.status, "PASS")
            self.assertEqual(first.accepted, 2)
            second = importer.flush()
            self.assertEqual(second.status, "NO_DATA")

            replacement = plugin_data / "replacement.jsonl"
            replacement.write_text(
                "\n".join(json.dumps(self._event(f"event-{index}")) for index in (1, 2, 3)) + "\n",
                encoding="utf-8",
            )
            os.replace(replacement, spool)
            third = importer.flush()
            self.assertEqual(third.accepted, 1)
            events = importer.ingestor.traces.list_events()
            self.assertEqual(len(events), 3)
            self.assertEqual({event.observation_source for event in events}, {"CODEX_HOOK"})

    def test_real_hook_error_reaches_existing_detector_and_mcp_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin_data = root / "plugin-data"
            plugin_data.mkdir()
            (plugin_data / "hook_events.jsonl").write_text(
                json.dumps(self._event("event-1", status="error", tool_name="mcp__shutu-workshop__workshop_health")) + "\n",
                encoding="utf-8",
            )
            service = WorkshopMcpService(root / "workspace", plugin_data=plugin_data)
            activity = service.recent_activity({})
            self.assertEqual(activity["hook_import"]["status"], "PASS")
            self.assertEqual(activity["activities"][0]["observation_source"], "CODEX_HOOK")
            issues = service.list_issues({})
            self.assertEqual(issues["data_status"], "OBSERVED")
            self.assertEqual(issues["issues"][0]["rule_id"], "R01")
            self.assertEqual(issues["issues"][0]["occurrence_count"], 1)
            self.assertEqual(DiagnosisService(root / "workspace").list_issues()[0].rule_id, "R01")


if __name__ == "__main__":
    unittest.main()
