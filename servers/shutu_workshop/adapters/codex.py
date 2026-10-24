"""Codex CLI JSONL adapter.

The adapter consumes the public ``codex exec --json`` stream. It does not read
private session files and does not invent hidden Skill or MCP events.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .base import AdapterEvent, BaseAdapter
from ..ingestion import TraceIngestor


def _string(value: Any) -> str:
    return str(value) if value is not None else ""


def _nested(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return ""


class CodexAdapter(BaseAdapter):
    adapter_id = "codex"
    adapter_version = "1"
    source_type = "codex-cli-jsonl"

    def __init__(self, ingestor: TraceIngestor, *, workspace_id: str = "local", host_version: str = "unknown", policy=None) -> None:
        super().__init__(
            ingestor,
            workspace_id=workspace_id,
            host_version=host_version or self.executable_version("codex", "--version"),
            policy=policy,
        )

    def supported_event_types(self) -> tuple[str, ...]:
        return (
            "task.started",
            "task.turn.started",
            "tool.started",
            "tool.result",
            "task.output",
            "task.finished",
            "host.error",
        )

    def missing_event_types(self) -> tuple[str, ...]:
        return (
            "skill.discovered",
            "skill.loaded",
            "child_agent.started",
            "mcp.tool_call",
            "mcp.tool_result",
            "model.usage",
        )

    def field_status(self) -> dict[str, str]:
        return {
            "host_event_id": "observed",
            "session_id": "observed",
            "turn_id": "observed",
            "request_id": "not_observed",
            "tool_identity": "observed",
            "transport": "inferred",
            "model": "not_observed",
            "skill_lifecycle": "not_observed",
            "desktop_app_events": "unsupported",
        }

    def normalize(self, raw: dict[str, Any], index: int, *, trace_id: str) -> AdapterEvent | None:
        event_type = _string(raw.get("type") or raw.get("event"))
        if not event_type:
            return None
        thread_id = _string(raw.get("thread_id") or raw.get("threadId"))
        turn_id = _string(raw.get("turn_id") or raw.get("turnId"))
        item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
        payload_record = raw.get("payload_json") if isinstance(raw.get("payload_json"), dict) else {}
        thread_id = thread_id or _string(item.get("thread_id") or item.get("threadId"))
        turn_id = turn_id or _string(item.get("turn_id") or item.get("turnId"))
        native_id = _string(raw.get("id") or item.get("id")) or f"line-{index}"
        occurred_at = _string(raw.get("timestamp") or raw.get("occurred_at"))
        if not occurred_at:
            occurred_at = _string(raw.get("created_at"))
        kind = _string(item.get("type"))
        phase = ""
        normalized_type = event_type
        actor = "agent"
        tool_name = ""
        request_id = _string(
            item.get("call_id")
            or item.get("callId")
            or item.get("tool_call_id")
            or raw.get("request_id")
            or payload_record.get("request_id")
            or payload_record.get("jsonrpc_id")
        )
        if event_type == "thread.started":
            normalized_type = "task.started"
        elif event_type == "turn.started":
            normalized_type = "task.turn.started"
        elif event_type == "turn.completed":
            normalized_type = "task.finished"
            turn = raw.get("turn") if isinstance(raw.get("turn"), dict) else {}
            status = _string(raw.get("status") or turn.get("status"))
        elif event_type in {"item.started", "item.completed"}:
            phase = event_type.rsplit(".", 1)[-1]
            if kind in {"command_execution", "shell", "tool_call", "mcp_tool_call"}:
                normalized_type = "tool.started" if phase == "started" else "tool.result"
                actor = "tool"
                tool_name = _string(item.get("name") or item.get("tool") or payload_record.get("name") or kind)
            elif kind in {"agent_message", "message"}:
                normalized_type = "task.output"
            else:
                normalized_type = f"host.item.{phase}"
        elif event_type in {"error", "turn.failed", "thread.error"}:
            normalized_type = "host.error"
            status = "error"
        else:
            status = _string(raw.get("status"))

        if not tool_name:
            tool_name = _string(
                raw.get("tool")
                or raw.get("name")
                or payload_record.get("tool")
                or payload_record.get("name")
            )
        if normalized_type.startswith("mcp.") or normalized_type.startswith("tool."):
            actor = "tool"

        if not occurred_at:
            occurred_at = _string(raw.get("time")) or ""
        payload = {
            "host_type": event_type,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_type": kind,
            "item": item,
            "payload": payload_record,
            "event": {key: value for key, value in raw.items() if key not in {"item"}},
        }
        status = locals().get("status", "") or _string(raw.get("status") or item.get("status") or payload_record.get("status"))
        if normalized_type == "host.error":
            error_type = "host.error"
        else:
            error_type = "" if status not in {"error", "failed"} else "host.error"
        correlation = "exact" if native_id else "unknown"
        if request_id:
            correlation = "exact"
        return AdapterEvent(
            type=normalized_type,
            host_event_id=f"{native_id}:{phase}" if phase else native_id,
            seq=index,
            actor=actor,
            session_id=thread_id,
            turn_id=turn_id,
            occurred_at=occurred_at,
            span_id=thread_id or trace_id,
            parent_span_id=turn_id,
            request_id=request_id,
            tool_name=tool_name,
            transport="codex-cli",
            status=status,
            error_type=error_type,
            payload_json=payload,
            source_metadata={
                "host_event_id": native_id,
                "host_event_type": event_type,
                "observation_source": "codex.exec.jsonl",
            },
            correlation_quality=correlation,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Codex --json JSONL stream into M1")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--trace-id", default="")
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args(argv)
    ingestor = TraceIngestor(args.workspace)
    adapter = CodexAdapter(ingestor)
    result = adapter.ingest_jsonl(args.jsonl.read_text(encoding="utf-8"), trace_id=args.trace_id, close=True)
    print(json.dumps({"result": result.to_dict(), "descriptor": adapter.descriptor().to_dict()}, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
