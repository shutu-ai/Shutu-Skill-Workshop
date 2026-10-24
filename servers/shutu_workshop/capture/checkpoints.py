"""Restart-safe, bounded JSONL capture.

The checkpoint is advanced only after the supplied ingest callback returns a
successful result. A changed anchor, truncation, or replacement file resets
the read position instead of guessing that old offsets still mean the same
events.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..models import IngestResult, IngestValidationError


@dataclass(frozen=True)
class Checkpoint:
    source_path: str
    offset: int = 0
    file_key: str = ""
    anchor_hash: str = ""
    updated_at: str = ""
    reset_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointedJSONL:
    """Read at most ``batch_size`` complete lines and commit offsets safely."""

    def __init__(self, checkpoint_path: str | Path, *, anchor_bytes: int = 1024) -> None:
        if anchor_bytes < 64:
            raise ValueError("anchor_bytes is too small")
        self.checkpoint_path = Path(checkpoint_path).resolve()
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.anchor_bytes = anchor_bytes

    def load(self, source_path: str | Path) -> Checkpoint:
        source = str(Path(source_path).resolve())
        if not self.checkpoint_path.exists():
            return Checkpoint(source_path=source)
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            checkpoint = Checkpoint(**payload)
        except (OSError, ValueError, TypeError):
            return Checkpoint(source_path=source)
        return checkpoint if checkpoint.source_path == source else Checkpoint(source_path=source)

    def read(self, source_path: str | Path, *, batch_size: int = 100) -> tuple[list[dict[str, Any]], Checkpoint, int]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        checkpoint = self.load(source)
        stat = source.stat()
        file_key = self._file_key(stat)
        reset = checkpoint.offset > stat.st_size or (
            checkpoint.offset and checkpoint.anchor_hash != self._anchor_hash(source, checkpoint.offset)
        )
        if checkpoint.file_key and checkpoint.file_key != file_key:
            reset = True
        offset = 0 if reset else checkpoint.offset
        reset_count = checkpoint.reset_count + int(reset)
        records: list[dict[str, Any]] = []
        next_offset = offset
        with source.open("rb") as handle:
            handle.seek(offset)
            while len(records) < batch_size:
                line_start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # A writer may still be appending this record.
                    break
                next_offset = handle.tell()
                if not line.strip():
                    continue
                try:
                    value = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise IngestValidationError(
                        f"invalid JSONL at byte offset {line_start}", rejected=1
                    ) from exc
                if not isinstance(value, dict):
                    raise IngestValidationError(
                        f"JSONL record at byte offset {line_start} is not an object", rejected=1
                    )
                records.append(value)
        candidate = Checkpoint(
            source_path=str(source),
            offset=next_offset,
            file_key=file_key,
            anchor_hash=self._anchor_hash(source, next_offset),
            updated_at="pending",
            reset_count=reset_count,
        )
        return records, candidate, next_offset

    def ingest(
        self,
        source_path: str | Path,
        ingest_records: Callable[[Iterable[dict[str, Any]]], IngestResult],
        *,
        batch_size: int = 100,
    ) -> IngestResult:
        records, candidate, _ = self.read(source_path, batch_size=batch_size)
        if not records:
            return IngestResult()
        try:
            result = ingest_records(records)
        except Exception as exc:
            # A failed transaction must leave the old offset in place.
            return IngestResult(rejected=len(records), errors=(type(exc).__name__,))
        if not result.ok:
            return result
        self._save(
            Checkpoint(
                source_path=candidate.source_path,
                offset=candidate.offset,
                file_key=candidate.file_key,
                anchor_hash=candidate.anchor_hash,
                updated_at=self._now(),
                reset_count=candidate.reset_count,
            )
        )
        return result

    def _save(self, checkpoint: Checkpoint) -> None:
        encoded = json.dumps(checkpoint.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
        fd, temp_name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".tmp", dir=self.checkpoint_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.checkpoint_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _anchor_hash(self, source: Path, offset: int) -> str:
        if offset <= 0:
            return ""
        start = max(0, offset - self.anchor_bytes)
        with source.open("rb") as handle:
            handle.seek(start)
            value = handle.read(offset - start)
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _file_key(stat: os.stat_result) -> str:
        # Creation/change time moves on ordinary appends on some platforms.
        return f"{getattr(stat, 'st_dev', 0)}:{getattr(stat, 'st_ino', 0)}"

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat(timespec="seconds")
