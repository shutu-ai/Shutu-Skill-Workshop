"""Thin, read-only MCP adapter over the existing Workshop Core services.

The adapter deliberately exposes summaries and evidence references instead of
raw prompts, tool arguments, tool results, or provider credentials.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ..capabilities import CapabilityRegistry
from ..capture import HookSpoolImporter
from ..diagnostics import DiagnosisService
from ..evolution import EvaluationService, EvolutionDiagnosisService, ProposalService
from ..release import ReleaseObservationService
from ..storage import M1Database, database_path
from ..traces import TraceStore


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_value(value.to_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _decode(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _limit(value: Any, default: int = 20) -> int:
    try:
        return max(1, min(int(value), 100))
    except (TypeError, ValueError):
        return default


class WorkshopMcpService:
    """Serve current workspace evidence without mutating it."""

    version = "0.1.0"

    def __init__(self, workspace: str | Path, *, plugin_data: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.plugin_data = Path(plugin_data).expanduser().resolve() if plugin_data else None
        self.db = M1Database(self.workspace)
        self.traces = TraceStore(self.db)
        self.capabilities = CapabilityRegistry(self.db)
        self.diagnosis = DiagnosisService(self.db)
        self.diagnoses = EvolutionDiagnosisService(self.db)
        self.proposals = ProposalService(self.db)
        self.evaluations = EvaluationService(self.db)
        self.observations = ReleaseObservationService(self.db)
        self.hook_importer = HookSpoolImporter(self.workspace, self.plugin_data) if self.plugin_data else None

    @property
    def db_path(self) -> Path:
        return database_path(self.workspace)

    def health(self, _arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        hook_import = self._flush_hooks()
        counts: dict[str, int] = {}
        latest_event = ""
        latest_trace = ""
        schema_version = 0
        with self.db.connection() as connection:
            schema_version = int(connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])
            for table in (
                "traces",
                "trace_events",
                "capabilities",
                "capability_versions",
                "issue_clusters",
                "real_agent_runs",
                "evaluation_runs",
                "release_links",
            ):
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            row = connection.execute("SELECT MAX(occurred_at) FROM trace_events").fetchone()
            latest_event = str(row[0] or "")
            row = connection.execute("SELECT MAX(started_at) FROM traces").fetchone()
            latest_trace = str(row[0] or "")

        hook = self._hook_state()
        data_status = "OBSERVED" if counts["traces"] or counts["trace_events"] else "NO_DATA"
        return {
            "status": "PASS",
            "data_status": data_status,
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core",
            "workspace": str(self.workspace),
            "database": {
                "path_present": self.db_path.is_file(),
                "schema_version": schema_version,
                "latest_schema_version": M1Database.latest_schema_version,
                "counts": counts,
                "latest_event_at": latest_event,
                "latest_trace_at": latest_trace,
            },
            "plugin": {
                "plugin_data_configured": self.plugin_data is not None,
                "hook_status": hook.get("status", "NOT_ACTIVE"),
                "hook_state": hook,
                "hook_import": hook_import,
            },
            "modes": {"passive": True, "review": True, "automatic_release": False},
        }

    def recent_activity(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        hook_import = self._flush_hooks()
        arguments = arguments or {}
        events = self.traces.list_events()
        events.sort(key=lambda event: (event.occurred_at, event.event_id), reverse=True)
        items = [
            {
                "trace_id": event.trace_id,
                "event_id": event.event_id,
                "occurred_at": event.occurred_at,
                "type": event.type,
                "actor": event.actor,
                "status": event.status,
                "error_type": event.error_type,
                "capability_id": event.capability_id,
                "capability_version_id": event.capability_version_id,
                "request_id_present": bool(event.request_id),
                "tool_name": event.tool_name,
                "transport": event.transport,
                "observation_source": event.observation_source,
                "correlation_quality": event.correlation_quality,
            }
            for event in events[: _limit(arguments.get("limit"))]
        ]
        return {
            "status": "PASS",
            "data_status": "OBSERVED" if items else "NO_DATA",
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core.trace_store",
            "activities": items,
            "count": len(items),
            "hook_import": hook_import,
        }

    def list_issues(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        hook_import = self._flush_hooks()
        # A live observer may persist an error immediately before this read;
        # reconcile the current trace before returning the issue ledger.
        try:
            for trace in self.traces.list_traces()[-100:]:
                events = self.traces.list_events(trace.trace_id)
                if any(event.status == "error" for event in events):
                    self.diagnosis.analyze_trace(trace.trace_id)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            pass
        arguments = arguments or {}
        issues = self.diagnosis.list_issues(
            status=str(arguments.get("status") or "") or None,
            workspace_id=str(arguments.get("workspace_id") or "") or None,
            rule_id=str(arguments.get("rule_id") or "") or None,
            limit=_limit(arguments.get("limit")),
        )
        values = [_json_value(issue) for issue in issues]
        return {
            "status": "PASS",
            "data_status": "OBSERVED" if values else "NO_ACTIONABLE_ISSUE",
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core.diagnosis",
            "issues": values,
            "count": len(values),
            "hook_import": hook_import,
        }

    def issue_detail(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        issue_id = str(arguments.get("issue_id") or "").strip()
        if not issue_id:
            return self._error("issue_id is required", code="INVALID_ARGUMENT")
        details = self.diagnosis.show_issue(issue_id)
        if details is None:
            return {
                "status": "NOT_FOUND",
                "data_status": "NOT_OBSERVABLE",
                "evidence_tier": "NOT_OBSERVABLE",
                "source": "workshop.core.diagnosis",
                "issue_id": issue_id,
            }
        diagnoses = [
            {
                "diagnosis_id": item.diagnosis_id,
                "issue_id": item.issue_id,
                "status": item.status,
                "confidence_grade": item.confidence_grade,
                "provider_id": item.provider_id,
                "model_id": item.model_id,
                "missing_evidence": list(item.missing_evidence),
                "recommended_action_types": [action.target_type for action in item.recommended_actions],
            }
            for item in self.diagnoses.list(issue_id)
        ]
        proposals = [
            {
                "proposal_id": item.proposal_id,
                "issue_id": item.issue_id,
                "diagnosis_id": item.diagnosis_id,
                "status": item.status,
                "category": item.category,
                "capability_id": item.capability_id,
                "capability_version_id": item.capability_version_id,
                "candidate_hash": item.candidate_hash,
                "reviewer": item.reviewer,
            }
            for item in self.proposals.list(issue_id)
        ]
        result = _json_value(details)
        result["diagnoses"] = diagnoses
        result["proposals"] = proposals
        result.update({"status": "PASS", "evidence_tier": "REAL_OBSERVED", "source": "workshop.core"})
        return result

    def capability_versions(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        capability_id = str(arguments.get("capability_id") or "").strip()
        capabilities = (
            [self.capabilities.get(capability_id)] if capability_id else self.capabilities.list()
        )
        values = []
        for capability in capabilities:
            if capability is None:
                continue
            values.append(
                {
                    "capability": _json_value(capability),
                    "versions": [_json_value(item) for item in self.capabilities.list_versions(capability.capability_id)],
                }
            )
        return {
            "status": "PASS",
            "data_status": "OBSERVED" if values else "NO_DATA",
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core.capabilities",
            "capabilities": values,
            "count": len(values),
        }

    def evaluation_report(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        run_id = str(arguments.get("run_id") or "").strip()
        if not run_id:
            with self.db.connection() as connection:
                row = connection.execute(
                    "SELECT run_id FROM evaluation_runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
                ).fetchone()
            run_id = str(row[0]) if row else ""
        if not run_id:
            return {
                "status": "NO_DATA",
                "data_status": "NO_DATA",
                "evidence_tier": "NOT_OBSERVABLE",
                "source": "workshop.core.evaluation",
                "report": None,
            }
        try:
            report = self.evaluations.report(run_id).to_dict()
        except ValueError:
            return self._error("evaluation run does not exist", code="NOT_FOUND", run_id=run_id)
        return {
            "status": "PASS",
            "data_status": "OBSERVED",
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core.evaluation",
            "run_id": run_id,
            "report": _json_value(report),
        }

    def release_status(self, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        del arguments
        with self.db.connection() as connection:
            links = connection.execute(
                """
                SELECT release_id, status, target_scope, target_skill_name,
                       candidate_artifact_hash, baseline_artifact_hash,
                       capability_version_id, m0_operation_id, m0_version_id,
                       rollback_version_id, created_at, updated_at
                FROM release_links ORDER BY updated_at DESC, release_id DESC LIMIT 20
                """
            ).fetchall()
            observations = connection.execute(
                """
                SELECT observation_id, release_id, conclusion, status, created_at, updated_at
                FROM release_observation_runs ORDER BY updated_at DESC, observation_id DESC LIMIT 20
                """
            ).fetchall()
            plans = connection.execute(
                """
                SELECT plan_id, proposal_id, evaluation_run_id, target_scope,
                       target_skill_name, state, created_at, updated_at
                FROM release_plans ORDER BY updated_at DESC, plan_id DESC LIMIT 20
                """
            ).fetchall()
        value = {
            "release_links": [dict(row) for row in links],
            "observation_runs": [dict(row) for row in observations],
            "plans": [dict(row) for row in plans],
        }
        return {
            "status": "PASS",
            "data_status": "OBSERVED" if any(value.values()) else "NO_DATA",
            "evidence_tier": "REAL_OBSERVED",
            "source": "workshop.core.release",
            **value,
        }

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handlers = {
            "workshop_health": self.health,
            "workshop_recent_activity": self.recent_activity,
            "workshop_list_issues": self.list_issues,
            "workshop_issue_detail": self.issue_detail,
            "workshop_capability_versions": self.capability_versions,
            "workshop_evaluation_report": self.evaluation_report,
            "workshop_release_status": self.release_status,
        }
        handler = handlers.get(name)
        if handler is None:
            return self._error(f"unknown tool: {name}", code="NOT_FOUND")
        try:
            return handler(arguments or {})
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            return self._error(type(exc).__name__, code="WORKSHOP_READ_FAILED")

    def _hook_state(self) -> dict[str, Any]:
        if self.plugin_data is None:
            return {"status": "NOT_ACTIVE"}
        state_path = self.plugin_data / "hook_state.json"
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "HOOKS_UNTRUSTED", "state_present": False}
        if not isinstance(value, dict):
            return {"status": "HOOKS_UNTRUSTED", "state_present": False}
        return {"status": "REAL_OBSERVED", "state_present": True, **{key: value[key] for key in ("captured", "dropped", "unknown", "last_event_at") if key in value}}

    def _flush_hooks(self) -> dict[str, Any]:
        if self.hook_importer is None:
            return {"status": "NOT_ACTIVE", "source": "CODEX_HOOK"}
        result = self.hook_importer.flush()
        value = result.to_dict()
        if result.accepted or result.duplicates:
            try:
                value["diagnosis"] = _json_value(self.diagnosis.analyze_new(limit=100).to_dict())
            except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
                value["diagnosis"] = {"status": "OBSERVATION_GAP", "error_type": type(exc).__name__}
        return value

    @staticmethod
    def _error(message: str, *, code: str, **extra: Any) -> dict[str, Any]:
        return {"status": "ERROR", "error_type": code, "message": message, **extra}


__all__ = ["WorkshopMcpService"]
