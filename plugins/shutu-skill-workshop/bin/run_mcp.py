"""Launch the Workshop MCP server from a plugin cache or a source checkout.

The source checkout path uses the real Workshop Core adapter. The small
standard-library fallback keeps an installed local package useful when the
repository source is not present beside the Codex plugin cache.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _find_source() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("SHUTU_WORKSHOP_SOURCE", "").strip()
    if configured:
        candidates.append(Path(configured))
    for start in (Path.cwd(), PLUGIN_ROOT, *PLUGIN_ROOT.parents):
        candidates.extend([start, *start.parents])
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "servers" / "shutu_workshop" / "mcp_server").is_dir():
            return candidate
    return None


def _decode(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        return 20


TOOLS = [
    {"name": "workshop_health", "description": "Read plugin, hook, database, and freshness health.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "workshop_recent_activity", "description": "Read bounded, observable recent trace activity without raw payloads.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    {"name": "workshop_list_issues", "description": "List real open diagnosis issues.", "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "workspace_id": {"type": "string"}, "rule_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    {"name": "workshop_issue_detail", "description": "Read one issue and its evidence references.", "inputSchema": {"type": "object", "properties": {"issue_id": {"type": "string"}}, "required": ["issue_id"]}},
    {"name": "workshop_capability_versions", "description": "Read capability snapshots and version lineage.", "inputSchema": {"type": "object", "properties": {"capability_id": {"type": "string"}}}},
    {"name": "workshop_evaluation_report", "description": "Read an evaluation report and preserve unknown results.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}}},
    {"name": "workshop_release_status", "description": "Read release and observation summaries.", "inputSchema": {"type": "object", "properties": {}}},
]


class PortableService:
    """Read the existing SQLite schema without creating a second store."""

    version = "0.1.0"

    def __init__(self, workspace: str | Path, plugin_data: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.db_path = self.workspace if self.workspace.suffix.casefold() == ".db" else self.workspace / ".skill_workshop" / "state.db"
        self.plugin_data = Path(plugin_data).expanduser().resolve() if plugin_data else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _rows(self, query: str, values: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def health(self, _args: dict[str, Any]) -> dict[str, Any]:
        if not self.db_path.is_file():
            return {"status": "PASS", "data_status": "NO_DATA", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "database": {"path_present": False, "schema_version": 0, "counts": {}}}
        with self._connect() as connection:
            schema = int(connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
            counts = {}
            for table in ("traces", "trace_events", "capabilities", "capability_versions", "issue_clusters", "real_agent_runs", "evaluation_runs", "release_links"):
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            latest = str(connection.execute("SELECT COALESCE(MAX(occurred_at), '') FROM trace_events").fetchone()[0])
        hook = {"status": "NOT_ACTIVE"}
        if self.plugin_data:
            hook = _decode((self.plugin_data / "hook_state.json").read_text(encoding="utf-8"), {"status": "HOOKS_UNTRUSTED"}) if (self.plugin_data / "hook_state.json").is_file() else {"status": "HOOKS_UNTRUSTED"}
        return {"status": "PASS", "data_status": "OBSERVED" if counts["traces"] or counts["trace_events"] else "NO_DATA", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "database": {"path_present": True, "schema_version": schema, "latest_schema_version": 10, "counts": counts, "latest_event_at": latest}, "plugin": {"hook_state": hook}}

    def recent_activity(self, args: dict[str, Any]) -> dict[str, Any]:
        rows = self._rows("SELECT trace_id, event_id, occurred_at, type, actor, status, error_type, capability_id, capability_version_id, request_id, tool_name, transport, observation_source, correlation_quality FROM trace_events ORDER BY occurred_at DESC, event_id DESC LIMIT ?", (_limit(args.get("limit")),))
        for row in rows:
            row["request_id_present"] = bool(row.pop("request_id", ""))
        return {"status": "PASS", "data_status": "OBSERVED" if rows else "NO_DATA", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "activities": rows, "count": len(rows)}

    def list_issues(self, args: dict[str, Any]) -> dict[str, Any]:
        conditions = ["archived_at = ''"]
        values: list[Any] = []
        for column in ("status", "workspace_id", "rule_id"):
            if args.get(column):
                conditions.append(f"{column} = ?")
                values.append(str(args[column]))
        values.append(_limit(args.get("limit")))
        rows = self._rows(f"SELECT issue_id, rule_id, rule_version, category, normalized_signature, workspace_id, primary_capability_id, first_seen_at, last_seen_at, severity, status, occurrence_count, confidence_grade, primary_owner_hypothesis, attribution_state, created_at, updated_at, row_version FROM issue_clusters WHERE {' AND '.join(conditions)} ORDER BY last_seen_at DESC, issue_id LIMIT ?", tuple(values))
        for row in rows:
            row["primary_owner_hypothesis"] = _decode(row["primary_owner_hypothesis"], {})
        return {"status": "PASS", "data_status": "OBSERVED" if rows else "NO_ACTIONABLE_ISSUE", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "issues": rows, "count": len(rows)}

    def issue_detail(self, args: dict[str, Any]) -> dict[str, Any]:
        issue_id = str(args.get("issue_id") or "")
        if not issue_id:
            return {"status": "ERROR", "error_type": "INVALID_ARGUMENT", "message": "issue_id is required"}
        issues = self._rows("SELECT issue_id, rule_id, rule_version, category, normalized_signature, workspace_id, primary_capability_id, first_seen_at, last_seen_at, severity, status, occurrence_count, confidence_grade, primary_owner_hypothesis, attribution_state, created_at, updated_at, row_version FROM issue_clusters WHERE issue_id = ?", (issue_id,))
        if not issues:
            return {"status": "NOT_FOUND", "data_status": "NOT_OBSERVABLE", "evidence_tier": "NOT_OBSERVABLE", "source": "portable.sqlite", "issue_id": issue_id}
        issue = issues[0]
        issue["primary_owner_hypothesis"] = _decode(issue["primary_owner_hypothesis"], {})
        occurrences = self._rows("SELECT occurrence_id, issue_id, trace_id, observed_at, rule_id, rule_version, capability_id, capability_version_id, evidence_refs_json, source_scope_json, error_fingerprint, unknown_fields_json, evidence_quality, created_at FROM issue_occurrences WHERE issue_id = ? ORDER BY observed_at, occurrence_id", (issue_id,))
        for row in occurrences:
            row["evidence_refs"] = _decode(row.pop("evidence_refs_json"), [])
            row["source_scope"] = _decode(row.pop("source_scope_json"), {})
            row["unknown_fields"] = _decode(row.pop("unknown_fields_json"), [])
        return {"status": "PASS", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "issue": issue, "occurrences": occurrences}

    def capability_versions(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("capability_id"):
            rows = self._rows("SELECT c.capability_id, c.kind, c.adapter_id, c.locator, c.discovered_from, c.discovered_at, v.version_id, v.parent_version_id, v.content_hash, v.schema_hash, v.source, v.provider_version, v.observed_at, v.environment_fingerprint, v.snapshot_ref FROM capabilities c LEFT JOIN capability_versions v ON v.capability_id = c.capability_id WHERE c.capability_id = ? ORDER BY v.observed_at, v.version_id", (str(args["capability_id"]),))
        else:
            rows = self._rows("SELECT c.capability_id, c.kind, c.adapter_id, c.locator, c.discovered_from, c.discovered_at, v.version_id, v.parent_version_id, v.content_hash, v.schema_hash, v.source, v.provider_version, v.observed_at, v.environment_fingerprint, v.snapshot_ref FROM capabilities c LEFT JOIN capability_versions v ON v.capability_id = c.capability_id ORDER BY c.capability_id, v.observed_at, v.version_id")
        return {"status": "PASS", "data_status": "OBSERVED" if rows else "NO_DATA", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "capabilities": rows, "count": len(rows)}

    def evaluation_report(self, args: dict[str, Any]) -> dict[str, Any]:
        run_id = str(args.get("run_id") or "")
        if not run_id:
            latest = self._rows("SELECT run_id FROM evaluation_runs ORDER BY started_at DESC, run_id DESC LIMIT 1")
            run_id = str(latest[0]["run_id"]) if latest else ""
        if not run_id:
            return {"status": "NO_DATA", "data_status": "NO_DATA", "evidence_tier": "NOT_OBSERVABLE", "source": "portable.sqlite", "report": None}
        run = self._rows("SELECT run_id, suite_id, candidate_id, proposal_id, baseline_fingerprint, candidate_fingerprint, suite_hash, mode, environment_fingerprint, status, verdict, error_type, started_at, finished_at, lease_until, attempt_count FROM evaluation_runs WHERE run_id = ?", (run_id,))
        if not run:
            return {"status": "ERROR", "error_type": "NOT_FOUND", "message": "evaluation run does not exist", "run_id": run_id}
        results = self._rows("SELECT case_id, side, oracle_verdict, task_success, duration_ms, tool_call_count, retry_count, token_count, cost, observation_completeness, execution_status, provenance, runner_id, runner_version, artifact_hash, environment_fingerprint FROM evaluation_case_results WHERE run_id = ? ORDER BY case_id, side", (run_id,))
        verdict = self._rows("SELECT verdict, statistics_json, regression_json, created_at FROM evaluation_verdicts WHERE run_id = ?", (run_id,))
        if verdict:
            verdict[0]["statistics"] = _decode(verdict[0].pop("statistics_json"), {})
            verdict[0]["regression"] = _decode(verdict[0].pop("regression_json"), {})
        return {"status": "PASS", "data_status": "OBSERVED", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "run_id": run_id, "report": {"run": run[0], "results": results, "verdict": verdict[0] if verdict else None}}

    def release_status(self, _args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "PASS", "data_status": "OBSERVED" if self.db_path.is_file() else "NO_DATA", "evidence_tier": "REAL_OBSERVED", "source": "portable.sqlite", "release_links": self._rows("SELECT release_id, status, target_scope, target_skill_name, candidate_artifact_hash, baseline_artifact_hash, capability_version_id, m0_operation_id, m0_version_id, rollback_version_id, created_at, updated_at FROM release_links ORDER BY updated_at DESC, release_id DESC LIMIT 20"), "observation_runs": self._rows("SELECT observation_id, release_id, conclusion, status, created_at, updated_at FROM release_observation_runs ORDER BY updated_at DESC, observation_id DESC LIMIT 20"), "plans": self._rows("SELECT plan_id, proposal_id, evaluation_run_id, target_scope, target_skill_name, state, created_at, updated_at FROM release_plans ORDER BY updated_at DESC, plan_id DESC LIMIT 20")}

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handlers = {"workshop_health": self.health, "workshop_recent_activity": self.recent_activity, "workshop_list_issues": self.list_issues, "workshop_issue_detail": self.issue_detail, "workshop_capability_versions": self.capability_versions, "workshop_evaluation_report": self.evaluation_report, "workshop_release_status": self.release_status}
        handler = handlers.get(name)
        return handler(args) if handler else {"status": "ERROR", "error_type": "NOT_FOUND", "message": f"unknown tool: {name}"}


def _response(request: dict[str, Any], *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
    value["error" if error is not None else "result"] = error if error is not None else (result or {})
    return value


def _run_fallback(workspace: str | Path, plugin_data: str | Path | None) -> int:
    service = PortableService(workspace, plugin_data)
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            request = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        method = str(request.get("method") or "")
        response = None
        if method == "initialize":
            response = _response(request, result={"protocolVersion": "2025-11-25", "serverInfo": {"name": "shutu-skill-workshop", "version": service.version}, "capabilities": {"tools": {"listChanged": False}}})
        elif method == "tools/list":
            response = _response(request, result={"tools": TOOLS})
        elif method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            result = service.call(str(params.get("name") or ""), params.get("arguments") if isinstance(params.get("arguments"), dict) else {})
            body = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}]}
            if result.get("status") == "ERROR":
                body["isError"] = True
            response = _response(request, result=body)
        elif method in {"initialized", "notifications/initialized", "notifications/cancelled", "$/cancelRequest", "$ /cancelRequest"}:
            response = None
        elif "id" in request:
            response = _response(request, error={"code": -32601, "message": "method not found"})
        if response is not None:
            sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--plugin-data", default=None)
    args = parser.parse_args(argv)
    source = _find_source()
    if source is not None:
        sys.path.insert(0, str(source / "servers"))
        try:
            from shutu_workshop.mcp_server.server import main as source_main

            source_args = []
            if args.workspace:
                source_args.extend(["--workspace", args.workspace])
            if args.plugin_data:
                source_args.extend(["--plugin-data", args.plugin_data])
            return source_main(source_args)
        except (ImportError, ModuleNotFoundError):
            pass
    workspace = args.workspace or os.environ.get("SHUTU_WORKSHOP_WORKSPACE") or os.getcwd()
    return _run_fallback(workspace, args.plugin_data or os.environ.get("PLUGIN_DATA"))


if __name__ == "__main__":
    raise SystemExit(main())
