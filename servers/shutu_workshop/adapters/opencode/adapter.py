"""OpenCode CLI/plugin event adapter.

V1 and V2 hook names are normalized here rather than leaking into M1 Core.
The adapter accepts the JSON event stream emitted by ``opencode run --format
json`` as well as small records produced by a local V1/V2 plugin bridge.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..base import AdapterEvent, BaseAdapter
from ...ingestion import TraceIngestor


def _string(value: Any) -> str:
    return str(value) if value is not None else ""


class OpenCodeAdapter(BaseAdapter):
    adapter_id = "opencode"
    adapter_version = "1"
    source_type = "opencode-v2-json"

    def __init__(
        self,
        ingestor: TraceIngestor,
        *,
        api_generation: str = "v2",
        workspace_id: str = "local",
        host_version: str = "unknown",
        policy=None,
    ) -> None:
        self.api_generation = api_generation.casefold()
        super().__init__(
            ingestor,
            workspace_id=workspace_id,
            host_version=host_version or self.executable_version("opencode", "--version"),
            policy=policy,
        )

    def supported_event_types(self) -> tuple[str, ...]:
        return (
            "session.created",
            "session.updated",
            "session.idle",
            "session.error",
            "tool.before",
            "tool.after",
            "task.started",
            "task.finished",
            "host.error",
        )

    def missing_event_types(self) -> tuple[str, ...]:
        return (
            "skill.discovered",
            "skill.loaded",
            "child_agent.started",
            "model.usage",
            "permission.decision",
        )

    def field_status(self) -> dict[str, str]:
        return {
            "host_event_id": "observed",
            "session_id": "observed",
            "turn_id": "not_observed",
            "request_id": "observed",
            "tool_identity": "observed",
            "transport": "inferred",
            "model": "not_observed",
            "skill_lifecycle": "not_observed",
            "plugin_api_generation": "observed",
        }

    def normalize(self, raw: dict[str, Any], index: int, *, trace_id: str) -> AdapterEvent | None:
        event_name = _string(raw.get("type") or raw.get("event") or raw.get("hook"))
        event_name = event_name or _string(raw.get("name"))
        if not event_name:
            return None
        part = raw.get("part") if isinstance(raw.get("part"), dict) else {}
        event = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        event = event or raw
        payload_record = raw.get("payload_json") if isinstance(raw.get("payload_json"), dict) else {}
        session_id = _string(raw.get("sessionID") or raw.get("session_id") or event.get("sessionID"))
        message_id = _string(
            raw.get("messageID")
            or raw.get("message_id")
            or part.get("messageID")
            or event.get("messageID")
        )
        call_id = _string(
            raw.get("callID")
            or raw.get("call_id")
            or part.get("callID")
            or part.get("call_id")
            or event.get("callID")
            or event.get("call_id")
            or payload_record.get("call_id")
            or payload_record.get("request_id")
        )
        native_id = _string(raw.get("id") or part.get("id") or message_id or call_id) or f"line-{index}"
        tool_name = _string(
            raw.get("tool")
            or raw.get("toolName")
            or part.get("tool")
            or part.get("toolName")
            or event.get("tool")
            or payload_record.get("name")
            or payload_record.get("tool")
        )
        status = _string(raw.get("status") or part.get("status") or event.get("status"))
        normalized_type = event_name
        actor = "agent"
        if event_name in {"step_start", "session.created", "session.started"}:
            normalized_type = "task.started"
        elif event_name in {"session.updated", "session.idle"}:
            normalized_type = event_name
        elif event_name in {"tool.execute.before", "tool.before", "tool.call", "tool_use", "tool.started"}:
            normalized_type = "tool.before"
            actor = "tool"
        elif event_name in {"tool.execute.after", "tool.after", "tool.result", "tool_result", "tool.completed"}:
            normalized_type = "tool.after"
            actor = "tool"
        elif event_name in {"step_finish", "session.completed", "session.finished"}:
            normalized_type = "task.finished"
        elif event_name in {"error", "session.error", "tool.error"}:
            normalized_type = "host.error" if event_name != "tool.error" else "tool.after"
            status = status or "error"
        elif event_name.startswith("session."):
            normalized_type = event_name

        if not tool_name and normalized_type in {"tool.before", "tool.after"}:
            tool_name = _string(part.get("name") or event.get("name"))
        request_id = call_id or _string(raw.get("request_id") or event.get("request_id") or payload_record.get("request_id"))
        occurred_at = _string(raw.get("timestamp") or raw.get("occurred_at"))
        if not occurred_at:
            occurred_at = _string(part.get("timestamp") or event.get("timestamp"))
        if not occurred_at:
            occurred_at = ""
        error_type = "" if status not in {"error", "failed"} else "host.error"
        if normalized_type == "host.error":
            error_type = "host.error"
        host_event_id = native_id
        if normalized_type in {"tool.before", "tool.after"}:
            host_event_id = f"{native_id}:{'before' if normalized_type == 'tool.before' else 'after'}"
        return AdapterEvent(
            type=normalized_type,
            host_event_id=host_event_id,
            seq=index,
            actor=actor,
            session_id=session_id,
            turn_id=_string(raw.get("turnID") or raw.get("turn_id")),
            occurred_at=occurred_at,
            span_id=session_id or trace_id,
            parent_span_id=message_id,
            request_id=request_id,
            tool_name=tool_name,
            transport="opencode-cli",
            status=status,
            error_type=error_type,
            payload_json={
                "host_type": event_name,
                "session_id": session_id,
                "message_id": message_id,
                "call_id": call_id,
                "tool": tool_name,
                "part": part,
                "payload": payload_record,
                "event": {key: value for key, value in raw.items() if key not in {"part", "data"}},
            },
            source_metadata={
                "host_event_id": native_id,
                "host_event_type": event_name,
                "plugin_api_generation": self.api_generation,
                "observation_source": "opencode.json/plugin.bridge",
            },
            correlation_quality="exact" if request_id or session_id else "unknown",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import an OpenCode JSON event stream into M1")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--api-generation", choices=("v1", "v2"), default="v2")
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args(argv)
    ingestor = TraceIngestor(args.workspace)
    adapter = OpenCodeAdapter(ingestor, api_generation=args.api_generation)
    result = adapter.ingest_jsonl(args.jsonl.read_text(encoding="utf-8"), trace_id=args.trace_id, close=True)
    print(json.dumps({"result": result.to_dict(), "descriptor": adapter.descriptor().to_dict()}, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
