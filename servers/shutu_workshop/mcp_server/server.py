"""Small MCP stdio server for the native plugin."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from ..observers.mcp.stdio import StdioFrameCodec
from .service import WorkshopMcpService


TOOLS = [
    {"name": "workshop_health", "description": "Read plugin, hook, database, and freshness health.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "workshop_recent_activity", "description": "Read bounded, observable recent trace activity without raw payloads.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    {"name": "workshop_list_issues", "description": "List real open diagnosis issues; returns NO_ACTIONABLE_ISSUE when none exist.", "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "workspace_id": {"type": "string"}, "rule_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    {"name": "workshop_issue_detail", "description": "Read one issue, evidence references, diagnosis summaries, and proposal lineage.", "inputSchema": {"type": "object", "properties": {"issue_id": {"type": "string"}}, "required": ["issue_id"]}},
    {"name": "workshop_capability_versions", "description": "Read Skill/MCP capability snapshots and version lineage.", "inputSchema": {"type": "object", "properties": {"capability_id": {"type": "string"}}}},
    {"name": "workshop_evaluation_report", "description": "Read the latest or requested evaluation report, including unknown results and evidence tier.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}}},
    {"name": "workshop_release_status", "description": "Read release plans, links, and post-release observation summaries.", "inputSchema": {"type": "object", "properties": {}}},
]


def _response(request: dict[str, Any], *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
    if error is not None:
        value["error"] = error
    else:
        value["result"] = result or {}
    return value


def handle_request(request: dict[str, Any], service: WorkshopMcpService) -> dict[str, Any] | None:
    method = str(request.get("method") or "")
    if method == "initialize":
        return _response(
            request,
            result={
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "shutu-skill-workshop", "version": service.version},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )
    if method in {"initialized", "notifications/initialized", "notifications/cancelled", "$/cancelRequest", "$ /cancelRequest"}:
        return None
    if method == "tools/list":
        return _response(request, result={"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return _response(request, error={"code": -32602, "message": "tool name is required"})
        result = service.call(name, arguments)
        is_error = result.get("status") == "ERROR"
        body: dict[str, Any] = {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}],
        }
        if is_error:
            body["isError"] = True
        return _response(request, result=body)
    if "id" in request:
        return _response(request, error={"code": -32601, "message": "method not found"})
    return None


def run_stdio(service: WorkshopMcpService) -> int:
    while True:
        frame = StdioFrameCodec.read(sys.stdin.buffer)
        if frame is None:
            break
        if not frame.strip():
            continue
        try:
            request = json.loads(StdioFrameCodec.payload(frame).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        response = handle_request(request, service)
        if response is not None:
            sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shutu Skill Workshop read-only MCP server")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--plugin-data", type=Path, default=None)
    args = parser.parse_args(argv)
    workspace = args.workspace or os.environ.get("SHUTU_WORKSHOP_WORKSPACE") or os.getcwd()
    plugin_data = args.plugin_data or os.environ.get("PLUGIN_DATA")
    return run_stdio(WorkshopMcpService(workspace, plugin_data=plugin_data))


if __name__ == "__main__":
    raise SystemExit(main())
