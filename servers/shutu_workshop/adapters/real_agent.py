"""Small, explicit Codex CLI runner for real-agent evidence collection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..models import Trace, TraceEvent, new_id, utc_now
from ..redaction import RedactionPolicy
from ..storage import M1Database
from ..traces import TraceStore
from .base import CaptureResult
from .codex import CodexAdapter
from ..ingestion import TraceIngestor


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RealAgentRunEvidence:
    case_id: str
    pair_id: str
    experiment_id: str
    host: str
    host_version: str
    model_id: str
    runner_id: str
    runner_version: str
    workspace_id: str
    environment_fingerprint: str
    baseline_or_candidate: str
    artifact_hash: str
    capability_version_id: str
    trace_ids: tuple[str, ...]
    event_refs: tuple[str, ...]
    observed_tool_calls: tuple[dict[str, Any], ...]
    missing_fields: tuple[str, ...]
    execution_status: str
    task_result: str
    oracle_result: dict[str, Any]
    observation_completeness: str
    source_evidence_tier: str
    session_run_digest: str
    execution_manifest_hash: str
    captured_at: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["trace_ids"] = list(self.trace_ids)
        value["event_refs"] = list(self.event_refs)
        value["observed_tool_calls"] = list(self.observed_tool_calls)
        value["missing_fields"] = list(self.missing_fields)
        return value


Oracle = Callable[[list[TraceEvent]], Mapping[str, Any] | str]


class CodexRealTaskRunner:
    """Run one bounded Codex task and persist only redacted evidence."""

    runner_id = "shutu.codex-real-task-runner"
    runner_version = "1"

    def __init__(
        self,
        workspace_or_db: str | Path | M1Database,
        *,
        codex_command: str = "codex",
        codex_home: str | Path | None = None,
        workspace_id: str = "m6-real-agent",
        environment_fingerprint: str = "",
        timeout: float = 120.0,
        cwd: str | Path | None = None,
        model_id: str = "",
        extra_args: Sequence[str] = (),
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.ingestor = TraceIngestor(self.db)
        self.traces = TraceStore(self.db, policy=RedactionPolicy())
        self.codex_command = shutil.which(codex_command) or codex_command
        self.codex_home = str(Path(codex_home).resolve()) if codex_home else ""
        self.workspace_id = workspace_id
        self.environment_fingerprint = environment_fingerprint or _digest(
            {"host": "windows", "runner": self.runner_id, "runner_version": self.runner_version}
        )[:24]
        self.timeout = max(1.0, float(timeout))
        self.cwd = Path(cwd).resolve() if cwd else None
        self.model_id = model_id
        self.extra_args = tuple(str(item) for item in extra_args)

    def configure_mcp_server(
        self,
        name: str,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Register a local fixture in the supplied isolated CODEX_HOME only."""

        if not self.codex_home:
            raise ValueError("configure_mcp_server requires an isolated codex_home")
        if not name or not command:
            raise ValueError("MCP server name and command are required")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = self.codex_home
        args = [self.codex_command, "mcp", "add"]
        for key, value in sorted((env or {}).items()):
            args.extend(["--env", f"{key}={value}"])
        args.extend([name, "--", *map(str, command)])
        completed = subprocess.run(args, env=environment, capture_output=True, text=True, timeout=30, check=False)
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout_summary": _digest(completed.stdout or ""),
            "stderr_summary": _digest(completed.stderr or ""),
        }

    def run(
        self,
        *,
        case_id: str,
        task_input: Any,
        baseline_or_candidate: str,
        artifact_hash: str,
        capability_version_id: str = "",
        pair_id: str = "",
        experiment_id: str = "",
        comparison_manifest_hash: str = "",
        task_category: str = "",
        agent_id: str = "codex",
        provider_model: str = "",
        oracle: Oracle | None = None,
    ) -> RealAgentRunEvidence:
        if baseline_or_candidate not in {"baseline", "candidate"}:
            raise ValueError("baseline_or_candidate must be baseline or candidate")
        if not case_id or not artifact_hash:
            raise ValueError("case_id and artifact_hash are required")
        pair_id = pair_id or new_id("pair")
        experiment_id = experiment_id or new_id("experiment")
        trace_id = new_id("real-agent")
        task_input_hash = _digest(task_input)
        execution_manifest_hash = _digest(
            {
                "case_id": case_id,
                "pair_id": pair_id,
                "experiment_id": experiment_id,
                "side": baseline_or_candidate,
                "artifact_hash": artifact_hash,
                "capability_version_id": capability_version_id,
                "environment_fingerprint": self.environment_fingerprint,
                "runner_id": self.runner_id,
                "runner_version": self.runner_version,
                "model_id": provider_model or self.model_id,
                "task_input_hash": task_input_hash,
                "comparison_manifest_hash": comparison_manifest_hash,
            }
        )
        command = [
            self.codex_command,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
        ]
        if self.cwd:
            command.extend(["--cd", str(self.cwd)])
        if provider_model or self.model_id:
            command.extend(["--model", provider_model or self.model_id])
        command.extend(self.extra_args)
        # The prompt exists only in the child process; the persisted identity is its digest.
        prompt = _canonical({"case_id": case_id, "task_input": task_input, "allowed_action": "local fixture only"})
        command.append(prompt)
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        if self.codex_home:
            environment["CODEX_HOME"] = self.codex_home
        started = time.perf_counter()
        adapter = CodexAdapter(
            self.ingestor,
            workspace_id=self.workspace_id,
            host_version=CodexAdapter.executable_version(self.codex_command, "--version"),
        )
        capture: CaptureResult = adapter.capture_stream(
            command,
            cwd=self.cwd,
            env=environment,
            timeout=self.timeout,
            trace_id=trace_id,
        )
        events = self.traces.list_events(trace_id)
        self._bind_trace(
            trace_id,
            {
                "case_id": case_id,
                "pair_id": pair_id,
                "experiment_id": experiment_id,
                "side": baseline_or_candidate,
                "artifact_hash": artifact_hash,
                "capability_version_id": capability_version_id,
                "comparison_manifest_hash": comparison_manifest_hash,
                "task_category": task_category,
                "agent_id": agent_id,
                "provider_model": provider_model or self.model_id,
                "runner_id": self.runner_id,
                "runner_version": self.runner_version,
                "source_evidence_tier": "REAL_AGENT_OBSERVED",
                "real_agent": True,
                "task_input_hash": task_input_hash,
                "host_version": adapter.host_version,
            },
        )
        oracle_result = self._run_oracle(oracle, events)
        verdict = str(oracle_result.get("verdict") or oracle_result.get("status") or "unknown")
        task_result = "success" if verdict in {"pass", "passed", "success", "ok"} else ("failed" if verdict in {"fail", "failed", "error"} else "unknown")
        blocked_env, blocked_reason = self._environment_blocked(capture, events)
        trace = self.traces.get(trace_id)
        if trace is not None:
            self.traces.close(
                trace_id,
                "ok" if capture.exit_code == 0 and not capture.timed_out else "failed",
                task_result=task_result,
                observation_completeness="complete" if capture.exit_code == 0 and not capture.timed_out else "partial",
            )
        event_refs = tuple(event.event_id for event in events)
        tool_calls = tuple(
            {
                "event_id": event.event_id,
                "type": event.type,
                "tool_name": event.tool_name,
                "request_id": event.request_id,
                "status": event.status,
            }
            for event in events
            if event.type in {"tool.started", "tool.result", "mcp.tool_call", "mcp.tool_result"}
        )
        session_run_digest = _digest({"trace_id": trace_id, "events": [event.to_dict() for event in events]})
        missing_fields = tuple(sorted(set(adapter.descriptor().missing_event_types) | {"skill_lifecycle"}))
        evidence = RealAgentRunEvidence(
            case_id=case_id,
            pair_id=pair_id,
            experiment_id=experiment_id,
            host="codex",
            host_version=adapter.host_version,
            model_id=provider_model or self.model_id,
            runner_id=self.runner_id,
            runner_version=self.runner_version,
            workspace_id=self.workspace_id,
            environment_fingerprint=self.environment_fingerprint,
            baseline_or_candidate=baseline_or_candidate,
            artifact_hash=artifact_hash,
            capability_version_id=capability_version_id,
            trace_ids=(trace_id,) if self.traces.get(trace_id) else (),
            event_refs=event_refs,
            observed_tool_calls=tool_calls,
            missing_fields=missing_fields,
            execution_status="completed" if capture.exit_code == 0 and not capture.timed_out else "blocked_env" if blocked_env else "failed",
            task_result=task_result,
            oracle_result=oracle_result,
            observation_completeness="complete" if capture.exit_code == 0 and not capture.timed_out else "partial",
            source_evidence_tier="REAL_AGENT_OBSERVED",
            session_run_digest=session_run_digest,
            execution_manifest_hash=execution_manifest_hash,
            captured_at=utc_now(),
            metrics={
                "walltime_ms": max(0, int((time.perf_counter() - started) * 1000)),
                "tool_calls": len(tool_calls),
                "retries": sum(1 for event in events if event.retry_of_event_id or (event.attempt_index or 0) > 0),
                "tokens": None,
                "cost": None,
                "exit_code": capture.exit_code,
                "timed_out": capture.timed_out,
                "stderr_digest": _digest(capture.stderr_summary),
                "stdout_digest": _digest(capture.stdout),
                "environment_blocked": blocked_env,
                "blocked_reason": blocked_reason,
            },
        )
        return self._persist(evidence)

    def run_pair(self, cases: Sequence[Mapping[str, Any]], **kwargs: Any) -> list[RealAgentRunEvidence]:
        """Run a fixed suite for both sides; Oracle stays outside the Runner."""

        results: list[RealAgentRunEvidence] = []
        pair_id = str(kwargs.pop("pair_id", "") or new_id("pair"))
        experiment_id = str(kwargs.pop("experiment_id", "") or new_id("experiment"))
        for case in cases:
            for side in ("baseline", "candidate"):
                results.append(
                    self.run(
                        case_id=str(case["case_id"]),
                        task_input=case.get("task_input"),
                        baseline_or_candidate=side,
                        artifact_hash=str(case[f"{side}_artifact_hash"]),
                        pair_id=pair_id,
                        experiment_id=experiment_id,
                        **kwargs,
                    )
                )
        return results

    def _bind_trace(self, trace_id: str, additions: dict[str, Any]) -> None:
        trace = self.traces.get(trace_id)
        if trace is None:
            self.traces.start(
                Trace(
                    trace_id=trace_id,
                    workspace_id=self.workspace_id,
                    adapter_id="codex",
                    adapter_version="1",
                    environment_fingerprint=self.environment_fingerprint,
                    source="codex-cli-jsonl",
                    metadata=additions,
                )
            )
            return
        metadata = dict(trace.metadata or {})
        metadata.update(additions)
        metadata = RedactionPolicy().redact_metadata(metadata)
        with self.db.transaction() as connection:
            connection.execute("UPDATE traces SET metadata_json = ? WHERE trace_id = ?", (_canonical(metadata), trace_id))

    def _run_oracle(self, oracle: Oracle | None, events: list[TraceEvent]) -> dict[str, Any]:
        if oracle is None:
            return {"verdict": "unknown", "reason": "independent_oracle_not_configured"}
        try:
            value = oracle(events)
            if isinstance(value, str):
                return {"verdict": value}
            return RedactionPolicy().redact_metadata(dict(value))
        except Exception as exc:  # Oracle failure is evidence, not a task pass.
            return {"verdict": "unknown", "error_type": type(exc).__name__}

    @staticmethod
    def _environment_blocked(capture: CaptureResult, events: list[TraceEvent]) -> tuple[bool, str]:
        if capture.timed_out:
            return True, "timeout"
        tool_events = [event for event in events if event.type in {"tool.started", "tool.result", "mcp.tool_call", "mcp.tool_result"}]
        text = f"{capture.stdout}\n{capture.stderr_summary}".casefold()
        if not tool_events and any(
            marker in text
            for marker in (
                "401 unauthorized",
                "403 forbidden",
                "missing bearer",
                "authentication",
                "connection refused",
                "network",
                "could not resolve",
            )
        ):
            return True, "provider_or_transport"
        return False, ""

    def _persist(self, evidence: RealAgentRunEvidence) -> RealAgentRunEvidence:
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO real_agent_runs(
                    run_id, case_id, pair_id, experiment_id, side, host, host_version,
                    model_id, runner_id, runner_version, workspace_id,
                    environment_fingerprint, baseline_or_candidate, artifact_hash,
                    capability_version_id, trace_id, execution_status, task_result,
                    oracle_result, observation_completeness, source_evidence_tier,
                    session_run_digest, execution_manifest_hash, captured_at,
                    metrics_json, missing_fields_json, event_refs_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.execution_manifest_hash,
                    evidence.case_id,
                    evidence.pair_id,
                    evidence.experiment_id,
                    evidence.baseline_or_candidate,
                    evidence.host,
                    evidence.host_version,
                    evidence.model_id,
                    evidence.runner_id,
                    evidence.runner_version,
                    evidence.workspace_id,
                    evidence.environment_fingerprint,
                    evidence.baseline_or_candidate,
                    evidence.artifact_hash,
                    evidence.capability_version_id,
                    evidence.trace_ids[0] if evidence.trace_ids else "",
                    evidence.execution_status,
                    evidence.task_result,
                    _canonical(evidence.oracle_result),
                    evidence.observation_completeness,
                    evidence.source_evidence_tier,
                    evidence.session_run_digest,
                    evidence.execution_manifest_hash,
                    evidence.captured_at,
                    _canonical(evidence.metrics),
                    _canonical(list(evidence.missing_fields)),
                    _canonical(list(evidence.event_refs)),
                    _canonical(evidence.to_dict()),
                ),
            )
            row = connection.execute(
                "SELECT payload_json FROM real_agent_runs WHERE execution_manifest_hash = ?",
                (evidence.execution_manifest_hash,),
            ).fetchone()
        return self._from_payload(json.loads(row[0])) if row else evidence

    @staticmethod
    def _from_payload(value: dict[str, Any]) -> RealAgentRunEvidence:
        names = set(RealAgentRunEvidence.__dataclass_fields__)
        payload = {key: item for key, item in value.items() if key in names}
        for name in ("trace_ids", "event_refs", "missing_fields"):
            payload[name] = tuple(payload.get(name, ()))
        payload["observed_tool_calls"] = tuple(payload.get("observed_tool_calls", ()))
        return RealAgentRunEvidence(**payload)


__all__ = ["CodexRealTaskRunner", "RealAgentRunEvidence"]
