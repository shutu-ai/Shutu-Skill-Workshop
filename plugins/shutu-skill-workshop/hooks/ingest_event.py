"""Bounded, fail-open Codex hook recorder for the Workshop plugin."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MAX_INPUT_BYTES = 256 * 1024
DEFAULT_RETENTION = 1000
MAX_RETENTION = 5000

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _retention() -> int:
    try:
        return max(1, min(int(os.environ.get("SHUTU_WORKSHOP_HOOK_RETENTION", DEFAULT_RETENTION)), MAX_RETENTION))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    handle = path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def _state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _summary(event: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    event_name = str(event.get("hook_event_name") or event.get("event_type") or "unknown")
    tool_name = str(event.get("tool_name") or "")
    request_id = event.get("request_id") or event.get("tool_use_id") or ""
    event_id = event.get("event_id") or event.get("id") or ""
    unknown = not bool(event_id or request_id or event_name != "unknown")
    input_value = event.get("tool_input")
    response_value = event.get("tool_response")
    response_status = ""
    if isinstance(response_value, dict):
        response_status = "error" if response_value.get("isError") or response_value.get("error") else "ok"
    summary = {
        "event_id": _hash(event_id or {"event": event_name, "request": request_id, "time": event.get("timestamp", "")}),
        "hook_event_name": event_name,
        "tool_name": tool_name,
        "session_id_hash": _hash(event.get("session_id", "")) if event.get("session_id") else "",
        "turn_id_hash": _hash(event.get("turn_id", "")) if event.get("turn_id") else "",
        "request_id_hash": _hash(request_id) if request_id else "",
        "cwd_hash": _hash(event.get("cwd", "")) if event.get("cwd") else "",
        "permission_mode": str(event.get("permission_mode") or ""),
        "argument_keys": sorted(str(key) for key in input_value) if isinstance(input_value, dict) else [],
        "result_status": response_status,
        "captured_at": _now(),
        "redaction": "summary_only",
    }
    return summary, unknown


def _write_summary(plugin_data: Path, summary: dict[str, Any], unknown: bool) -> None:
    plugin_data.mkdir(parents=True, exist_ok=True)
    events_path = plugin_data / "hook_events.jsonl"
    state_path = plugin_data / "hook_state.json"
    lock_path = plugin_data / "hook.lock"
    with _lock(lock_path):
        lines: list[str] = []
        if events_path.is_file():
            lines = events_path.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
        retention = _retention()
        dropped = max(0, len(lines) - retention)
        lines = lines[-retention:]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=plugin_data, delete=False) as handle:
            handle.write("\n".join(lines) + "\n")
            temporary = Path(handle.name)
        os.replace(temporary, events_path)

        current = _state(state_path)
        current.update(
            {
                "status": "REAL_OBSERVED",
                "captured": int(current.get("captured", 0)) + 1,
                "dropped": int(current.get("dropped", 0)) + dropped,
                "unknown": int(current.get("unknown", 0)) + int(unknown),
                "last_event_at": summary["captured_at"],
                "retained": len(lines),
                "retention": retention,
            }
        )
        temporary_state = state_path.with_suffix(".tmp")
        temporary_state.write_text(json.dumps(current, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary_state, state_path)


def main() -> int:
    """Never block or fail the host workflow because observation is unavailable."""

    plugin_data = str(os.environ.get("PLUGIN_DATA") or "").strip()
    if not plugin_data:
        return 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        value = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        if not isinstance(value, dict):
            return 0
        summary, unknown = _summary(value)
        _write_summary(Path(plugin_data).resolve(), summary, unknown)
    except Exception:
        # Hook observation is deliberately fail-open; no stderr/stdout leakage.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
