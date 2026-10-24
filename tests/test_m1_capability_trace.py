from __future__ import annotations

import json
import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shutu_workshop import (
    Capability,
    CapabilityRegistry,
    ConflictError,
    IngestValidationError,
    M1Database,
    PayloadLimitError,
    RedactionPolicy,
    Trace,
    TraceEvent,
    TraceIngestor,
    TraceStore,
    ValidationError,
    content_hash,
    create_ingest_server,
    serve_in_thread,
)


def _append_m1_process(db_path: str, seq: int, result_queue: object) -> None:
    from shutu_workshop import TraceEvent, TraceStore

    try:
        TraceStore(db_path).append(
            TraceEvent(
                trace_id="process-trace",
                seq=seq,
                type="tool.result",
                adapter_event_id=f"process-source-{seq}",
                payload_json={"seq": seq},
            )
        )
        result_queue.put("ok")
    except BaseException as exc:  # pragma: no cover - parent assertion reports it
        result_queue.put(type(exc).__name__)


class M1CapabilityTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name) / "workspace"
        self.workspace.mkdir()
        self.db = M1Database(self.workspace)
        self.registry = CapabilityRegistry(self.db)
        self.registry.register_skill_version(
            "demo",
            package_hash="hash-v1",
            version_id="skill-version-1",
            source="m0-version",
        )
        self.registry.register_mcp_tool_snapshot(
            "demo-server",
            {
                "name": "search",
                "description": "Search fixture data",
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            version_id="mcp-tool-version-1",
        )
        self.ingestor = TraceIngestor(self.db)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_and_legacy_migrations_are_additive_and_idempotent(self) -> None:
        with self.db.connection() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertTrue({"issues", "proposals", "versions", "release_operations"}.issubset(tables))
        self.assertTrue({"capabilities", "capability_versions", "traces", "trace_events"}.issubset(tables))
        self.assertEqual(versions[:4], [1, 2, 3, 4])
        self.assertGreaterEqual(versions[-1], 6)
        M1Database(self.workspace)
        with self.db.connection() as connection:
            self.assertEqual(
                [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")],
                [1, 2, 3, 4, 5, 6, 7],
            )
        from plugins.daagent.skill_workshop.store import WorkshopStore

        legacy_view = WorkshopStore(self.workspace / ".skill_workshop")
        self.assertEqual(legacy_view.list_release_operations(), [])

        legacy_root = Path(self.temp_dir.name) / "legacy" / ".skill_workshop"
        legacy_root.mkdir(parents=True)
        connection = sqlite3.connect(legacy_root / "state.db")
        connection.executescript(
            """
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00+00:00');
            CREATE TABLE proposals(
                id TEXT PRIMARY KEY, skill_name TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            INSERT INTO proposals VALUES ('old', 'demo', 'pending_review', 'a', 'a', '{}');
            """
        )
        connection.commit()
        connection.close()
        M1Database(legacy_root)
        connection = sqlite3.connect(legacy_root / "state.db")
        self.assertEqual(connection.execute("SELECT count(*) FROM proposals WHERE id='old'").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT count(*) FROM sqlite_master WHERE name='trace_events'").fetchone()[0], 1)
        connection.close()

    def test_capability_lineage_and_mcp_schema_snapshot(self) -> None:
        first = self.registry.get_version("skill-version-1")
        self.assertIsNotNone(first)
        second = self.registry.snapshot(
            "skill:demo",
            version_id="skill-version-2",
            parent_version_id="skill-version-1",
            content_hash="hash-v2",
            source="release",
        )
        self.assertEqual(second.parent_version_id, "skill-version-1")
        self.assertEqual([item.version_id for item in self.registry.list_versions("skill:demo")], ["skill-version-1", "skill-version-2"])
        mcp = self.registry.get_version("mcp-tool-version-1")
        self.assertEqual(mcp.provider_version, "unknown")
        self.assertEqual(
            mcp.schema_hash,
            content_hash({"type": "object", "properties": {"query": {"type": "string"}}}),
        )

    def test_codex_and_opencode_jsonl_share_schema_and_redact_at_capture(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        codex = self.ingestor.import_jsonl(fixtures / "codex_trace.jsonl")
        self.assertEqual(codex.accepted, 7)
        events = self.ingestor.traces.list_events("trace-codex-001")
        self.assertEqual([event.seq for event in events], list(range(6)))
        self.assertEqual(events[3].error_type, "network.error")
        self.assertEqual(events[4].retry_of_event_id, "codex-evt-2")
        opencode = self.ingestor.import_jsonl(fixtures / "opencode_trace.jsonl")
        self.assertEqual(opencode.accepted, 4)
        self.assertEqual(
            self.ingestor.traces.list_traces(adapter_id="opencode-simulated")[0].adapter_id,
            "opencode-simulated",
        )
        with self.db.connection() as connection:
            raw = " ".join(row[0] for row in connection.execute("SELECT payload_json FROM trace_events"))
            raw += " " + " ".join(row[0] for row in connection.execute("SELECT metadata_json FROM traces"))
        self.assertNotIn("simulated-token", raw)
        self.assertNotIn("simulated-password", raw)
        self.assertNotIn("C:\\Users\\simulated-user", raw)
        self.assertEqual(self.ingestor.traces.get("trace-codex-001").task_result, "unknown")

    def test_idempotency_conflict_and_duplicate_seq_are_explicit(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "opencode_trace.jsonl"
        first = self.ingestor.import_jsonl(fixture)
        self.assertEqual(first.accepted, 4)
        duplicate = self.ingestor.import_jsonl(fixture)
        self.assertEqual(duplicate.accepted, 0)
        self.assertEqual(duplicate.duplicates, 4)
        conflicting = {
            "record_type": "event",
            "event": {
                "trace_id": "trace-opencode-001",
                "seq": 1,
                "event_id": "different-event-id",
                "adapter_event_id": "different-source-id",
                "type": "tool.call",
                "actor": "tool",
                "payload_json": {"query": "changed"},
            },
        }
        result = self.ingestor.ingest_records([conflicting])
        self.assertEqual(result.conflicts, 1)
        self.assertEqual(len(self.ingestor.traces.list_events("trace-opencode-001")), 3)

    def test_batch_validation_rolls_back_and_close_preserves_unknown_result(self) -> None:
        trace = Trace(trace_id="rollback-trace", workspace_id="ws", adapter_id="fixture")
        with self.assertRaises(ValidationError):
            self.ingestor.ingest_records(
                [
                    {"record_type": "trace", "trace": trace.to_dict()},
                    {"record_type": "event", "event": {"trace_id": trace.trace_id, "seq": 0}},
                ]
            )
        self.assertIsNone(self.ingestor.traces.get(trace.trace_id))
        self.ingestor.start(Trace(trace_id="close-trace", workspace_id="ws", adapter_id="fixture"))
        closed = self.ingestor.close("close-trace", "incomplete", "2026-10-24T12:00:00+00:00")
        self.assertEqual(closed.task_result, "unknown")
        self.assertEqual(closed.status, "incomplete")
        child = self.ingestor.start(
            Trace(
                trace_id="child-trace",
                workspace_id="ws",
                adapter_id="fixture",
                parent_trace_id="parent-not-observed",
                observation_completeness="partial",
            )
        )
        self.assertEqual(child.parent_trace_id, "parent-not-observed")
        self.assertIsNone(self.ingestor.traces.get("parent-not-observed"))

    def test_queries_and_concurrent_writes(self) -> None:
        trace = Trace(trace_id="concurrent-trace", workspace_id="ws", adapter_id="agent-a")
        self.ingestor.start(trace)
        errors: list[BaseException] = []

        def write(seq: int) -> None:
            try:
                self.ingestor.append(
                    TraceEvent(
                        trace_id=trace.trace_id,
                        seq=seq,
                        type="tool.result",
                        actor="tool",
                        adapter_event_id=f"source-{seq}",
                        capability_id="mcp-tool:demo-server/search",
                        capability_version_id="mcp-tool-version-1",
                        error_type="" if seq else "network.error",
                        payload_json={"seq": seq},
                    )
                )
            except BaseException as exc:  # pragma: no cover - assertion below reports failures
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(seq,)) for seq in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.ingestor.traces.list_events(trace.trace_id)), 8)
        self.assertEqual(len(self.ingestor.traces.search(adapter_id="agent-a", error_type="network.error")), 1)

    def test_process_writes_use_sqlite_busy_timeout_and_do_not_corrupt_db(self) -> None:
        self.ingestor.start(Trace(trace_id="process-trace", workspace_id="ws", adapter_id="agent-a"))
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_append_m1_process,
                args=(str(self.db.db_path), seq, result_queue),
            )
            for seq in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(10)
        self.assertEqual([process.exitcode for process in processes], [0, 0])
        self.assertEqual(sorted(result_queue.get(timeout=5) for _ in processes), ["ok", "ok"])
        self.assertEqual(len(self.ingestor.traces.list_events("process-trace")), 2)

    def test_http_requires_token_and_uses_same_ingestion_result(self) -> None:
        server = create_ingest_server(self.ingestor, token="local-test-token")
        thread = serve_in_thread(server)
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/v1/traces",
                data=json.dumps({"trace_id": "http-trace", "workspace_id": "ws", "adapter_id": "http"}).encode(),
                method="POST",
            )
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(request, timeout=5)
            self.assertEqual(unauthorized.exception.code, 401)
            unauthorized.exception.close()
            request.add_header("Authorization", "Bearer local-test-token")
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["accepted"], 1)
            event_request = Request(
                f"http://127.0.0.1:{server.server_port}/v1/events:batch",
                data=json.dumps(
                    [{
                        "trace_id": "http-trace",
                        "seq": 0,
                        "type": "task.started",
                        "adapter_event_id": "http-event-1",
                        "payload_json": {"authorization": "Bearer should-not-persist"},
                    }]
                ).encode(),
                headers={"X-Workshop-Token": "local-test-token"},
                method="POST",
            )
            with urlopen(event_request, timeout=5) as response:
                body = response.read().decode()
                self.assertEqual(json.loads(body)["accepted"], 1)
                self.assertNotIn("should-not-persist", body)
            with self.db.connection() as connection:
                self.assertNotIn(
                    "should-not-persist",
                    connection.execute("SELECT payload_json FROM trace_events").fetchone()[0],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_direct_trace_append_returns_conflict_instead_of_overwrite(self) -> None:
        store = TraceStore(self.db)
        store.start(Trace(trace_id="direct-trace", workspace_id="ws", adapter_id="agent"))
        event = TraceEvent(trace_id="direct-trace", seq=0, type="task.started", payload_json={"x": 1})
        store.append(event)
        with self.assertRaises(ConflictError):
            store.append(TraceEvent(trace_id="direct-trace", seq=0, type="task.finished", payload_json={"x": 2}))

    def test_invalid_json_and_payload_limits_are_bounded(self) -> None:
        with self.assertRaises(IngestValidationError):
            self.ingestor.import_jsonl_text('{"record_type":')
        limited_db = M1Database(Path(self.temp_dir.name) / "limited")
        limited = TraceIngestor(
            limited_db,
            policy=RedactionPolicy(max_payload_bytes=256, max_string_length=64),
        )
        limited.start(Trace(trace_id="limited-trace", workspace_id="ws", adapter_id="test"))
        with self.assertRaises(PayloadLimitError):
            limited.append(
                TraceEvent(
                    trace_id="limited-trace",
                    seq=0,
                    type="tool.result",
                    payload_json={"values": ["x" * 100 for _ in range(10)]},
                )
            )
        self.assertEqual(limited.traces.list_events("limited-trace"), [])

    def test_payload_retention_deletes_body_without_deleting_event(self) -> None:
        self.ingestor.start(Trace(trace_id="retention-trace", workspace_id="ws", adapter_id="test"))
        self.ingestor.append(
            TraceEvent(
                trace_id="retention-trace",
                seq=0,
                type="task.started",
                occurred_at="2026-01-01T00:00:00+00:00",
                payload_json={"prompt": "old fixture"},
            )
        )
        self.assertEqual(
            self.ingestor.traces.delete_payloads("2026-02-01T00:00:00+00:00"),
            1,
        )
        event = self.ingestor.traces.list_events("retention-trace")[0]
        self.assertEqual(event.payload_json, {"retention": "deleted"})


if __name__ == "__main__":
    unittest.main()
