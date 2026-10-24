from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shutu_workshop import Trace, TraceEvent, TraceIngestor
from shutu_workshop.capture import CheckpointedJSONL


class M2CheckpointTests(unittest.TestCase):
    def test_bounded_batches_advance_only_after_successful_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "events.jsonl"
            checkpoint = CheckpointedJSONL(root / "capture.checkpoint.json")
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"record_type": "trace", "trace": {"trace_id": "cp", "workspace_id": "ws", "adapter_id": "fixture"}}),
                        json.dumps({"record_type": "event", "event": {"trace_id": "cp", "seq": 0, "type": "task.started"}}),
                        json.dumps({"record_type": "event", "event": {"trace_id": "cp", "seq": 1, "type": "task.finished"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            ingestor = TraceIngestor(root / "workspace")
            first = checkpoint.ingest(source, ingestor.ingest_records, batch_size=2)
            self.assertTrue(first.ok)
            self.assertEqual(first.accepted, 2)
            saved = checkpoint.load(source)
            self.assertGreater(saved.offset, 0)
            second = checkpoint.ingest(source, ingestor.ingest_records, batch_size=2)
            self.assertTrue(second.ok)
            self.assertEqual(second.accepted, 1)
            self.assertEqual(len(ingestor.traces.list_events("cp")), 2)

    def test_truncation_or_rewrite_repositions_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "events.jsonl"
            checkpoint = CheckpointedJSONL(root / "capture.checkpoint.json")
            source.write_text(
                json.dumps({"record_type": "trace", "trace": {"trace_id": "old", "workspace_id": "ws", "adapter_id": "fixture"}}) + "\n",
                encoding="utf-8",
            )
            ingestor = TraceIngestor(root / "workspace")
            checkpoint.ingest(source, ingestor.ingest_records)
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"record_type": "trace", "trace": {"trace_id": "new", "workspace_id": "ws", "adapter_id": "fixture"}}),
                        json.dumps({"record_type": "event", "event": {"trace_id": "new", "seq": 0, "type": "task.started"}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = checkpoint.ingest(source, ingestor.ingest_records)
            self.assertTrue(result.ok)
            self.assertIsNotNone(ingestor.traces.get("new"))
            self.assertGreaterEqual(checkpoint.load(source).reset_count, 1)

    def test_failed_batch_does_not_advance_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "events.jsonl"
            source.write_text(json.dumps({"record_type": "event", "event": {"trace_id": "missing", "seq": 0, "type": "tool.call"}}) + "\n", encoding="utf-8")
            checkpoint = CheckpointedJSONL(root / "capture.checkpoint.json")
            ingestor = TraceIngestor(root / "workspace")
            result = checkpoint.ingest(source, ingestor.ingest_records)
            self.assertFalse(result.ok)
            self.assertFalse(checkpoint.checkpoint_path.exists())


if __name__ == "__main__":
    unittest.main()
