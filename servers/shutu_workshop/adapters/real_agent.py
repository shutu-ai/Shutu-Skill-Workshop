"""Small, explicit Codex CLI runner for real-agent evidence collection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
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
from ..observers.mcp.common import McpTraceRecorder
from ..observers.mcp.stdio import StdioFrameCodec, StdioObserver


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactActivationRecord:
    experiment_id: str
    pair_id: str
    case_id: str
    side: str
    immutable_artifact_ref: str
    capability_version_id: str
    expected_artifact_hash: str
    actual_prelaunch_hash: str
    actual_runtime_hash_or_unknown: str
    server_command_fingerprint: str
    mcp_server_instance_id: str
    mcp_connection_scope: dict[str, Any]
    codex_home_config_fingerprint: str
    runner_version: str
    environment_fingerprint: str
    activation_status: str
    activation_evidence_refs: tuple[str, ...]
    activated_at: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["activation_evidence_refs"] = list(self.activation_evidence_refs)
        return value


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
    activation_status: str = "UNVERIFIED"
    activation_record: dict[str, Any] = field(default_factory=dict)
    execution_evidence_tier: str = "REAL_AGENT_OBSERVED"
    failure_layer: str = ""

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
    runner_version = "2"

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
        codex_home: str | Path | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Register a local fixture in the supplied isolated CODEX_HOME only."""

        active_home = str(Path(codex_home).resolve()) if codex_home else self.codex_home
        if not active_home:
            raise ValueError("configure_mcp_server requires an isolated codex_home")
        Path(active_home).mkdir(parents=True, exist_ok=True)
        if not name or not command:
            raise ValueError("MCP server name and command are required")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = active_home
        args = [self.codex_command, "mcp", "add"]
        for key, value in sorted((env or {}).items()):
            args.extend(["--env", f"{key}={value}"])
        args.extend([name, "--", *map(str, command)])
        try:
            completed = subprocess.run(
                args,
                env=environment,
                capture_output=True,
                text=True,
                timeout=min(30.0, float(timeout)) if timeout else 30.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "exit_code": None,
                "stdout_summary": "",
                "stderr_summary": "",
                "error_class": "TimeoutExpired",
                "codex_home_config_fingerprint": self._config_fingerprint(Path(active_home)),
            }
        except OSError as exc:
            return {
                "ok": False,
                "exit_code": None,
                "stdout_summary": "",
                "stderr_summary": "",
                "error_class": type(exc).__name__,
                "codex_home_config_fingerprint": self._config_fingerprint(Path(active_home)),
            }
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout_summary": _digest(completed.stdout or ""),
            "stderr_summary": _digest(completed.stderr or ""),
            "codex_home_config_fingerprint": self._config_fingerprint(Path(active_home)),
        }

    @staticmethod
    def _artifact_hash(root: str | Path) -> str:
        from ..release.artifacts import artifact_tree_hash

        return artifact_tree_hash(root)

    @staticmethod
    def _config_fingerprint(root: Path) -> str:
        if not root.exists():
            return _digest({"missing": str(root)})
        entries: list[dict[str, Any]] = []
        for item in sorted(root.rglob("*")):
            if item.is_file():
                try:
                    stat = item.stat()
                    entries.append(
                        {
                            "path": item.relative_to(root).as_posix(),
                            "size": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        }
                    )
                except OSError:
                    entries.append({"path": item.name, "unreadable": True})
        return _digest(entries)

    @staticmethod
    def _command_belongs_to_artifact(command: Sequence[str], root: Path, cwd: Path | None) -> bool:
        root = root.resolve()
        for value in command:
            candidate = Path(str(value))
            if candidate.suffix.casefold() not in {".py", ".js", ".mjs", ".cjs", ".exe", ".cmd", ".bat"}:
                continue
            try:
                resolved = (candidate if candidate.is_absolute() else (cwd or Path.cwd()) / candidate).resolve()
                if resolved.is_file() and resolved.is_relative_to(root):
                    return True
            except (OSError, ValueError):
                continue
        return False

    def probe_mcp_server(
        self,
        command: Sequence[str],
        *,
        server_name: str = "shutu-fixture",
        connection_scope: Mapping[str, Any] | None = None,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Perform one real stdio initialize/list/call exchange and keep only evidence."""

        if not command:
            return {"ok": False, "error_class": "missing_command", "event_refs": ()}
        trace_id = new_id("mcp-activation")
        recorder = McpTraceRecorder(
            self.ingestor,
            workspace_id=self.workspace_id,
            trace_id=trace_id,
            adapter_id=f"m6-activation-mcp-{trace_id}",
            adapter_version=self.runner_version,
            server_name=server_name,
            transport="stdio",
        )
        scope = dict(connection_scope or {})
        scope.setdefault("workspace_id", self.workspace_id)
        scope.setdefault("run_id", trace_id)
        scope.setdefault("mcp_server", server_name)
        scope.setdefault("mcp_connection", trace_id)
        scope.setdefault("transport", "stdio")
        requests = (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "m6-activation"}},
            },
        )
        payload = b"".join(
            (json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            for request in requests
        )
        try:
            output, report = StdioObserver(
                command,
                recorder=recorder,
                cwd=cwd,
                env=env,
                timeout=timeout or self.timeout,
            ).proxy_bytes(payload)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {
                "ok": False,
                "error_class": type(exc).__name__,
                "event_refs": (),
                "trace_id": trace_id,
                "connection_scope": scope,
            }
        messages: list[dict[str, Any]] = []
        for line in output.splitlines():
            try:
                value = json.loads(StdioFrameCodec.payload(line).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                messages.append(value)
        def response(request_id: int) -> dict[str, Any] | None:
            return next((value for value in messages if value.get("id") == request_id), None)

        initialize = response(1)
        tools_list = response(2)
        tool_call = response(3)
        events = self.traces.list_events(trace_id)
        return {
            "ok": bool(initialize and isinstance(initialize.get("result"), dict) and tools_list),
            "initialize": bool(initialize and isinstance(initialize.get("result"), dict)),
            "tools_list": bool(tools_list and isinstance(tools_list.get("result"), dict)),
            "tool_call": bool(tool_call),
            "tool_result": bool(tool_call and ("result" in tool_call or "error" in tool_call)),
            "tool_names": [
                str(tool.get("name"))
                for tool in ((tools_list or {}).get("result", {}).get("tools", []) if tools_list else [])
                if isinstance(tool, dict) and tool.get("name")
            ],
            "trace_id": trace_id,
            "event_refs": tuple(event.event_id for event in events),
            "connection_scope": scope,
            "report": {
                "process_exit_code": report.process_exit_code,
                "timed_out": report.timed_out,
                "request_count": report.request_count,
                "response_count": report.response_count,
                "observed_messages": report.observed_messages,
                "errors": list(report.errors),
            },
        }

    def activate_artifact(
        self,
        *,
        experiment_id: str,
        pair_id: str,
        case_id: str,
        side: str,
        artifact_root: str | Path | None,
        expected_artifact_hash: str,
        capability_version_id: str = "",
        server_command: Sequence[str] = (),
        server_name: str = "",
        codex_home: str | Path | None = None,
        environment_fingerprint: str | None = None,
    ) -> ArtifactActivationRecord:
        """Verify the bytes and, when configured, probe the actual side server."""

        side = str(side)
        if side not in {"baseline", "candidate"}:
            raise ValueError("side must be baseline or candidate")
        root = Path(artifact_root).resolve() if artifact_root else None
        actual_prelaunch = ""
        reason = ""
        status = "UNVERIFIED"
        if root is None:
            reason = "artifact_root_not_supplied"
        else:
            try:
                actual_prelaunch = self._artifact_hash(root)
            except (OSError, ValueError) as exc:
                reason = type(exc).__name__
                status = "BLOCKED_ENV"
            else:
                if actual_prelaunch != expected_artifact_hash:
                    status = "MISMATCH"
                    reason = "declared_vs_actual_hash"
                elif not server_command:
                    reason = "server_command_not_supplied"
                elif not self._command_belongs_to_artifact(server_command, root, root):
                    reason = "server_command_not_bound_to_artifact"
                else:
                    scope = {
                        "workspace_id": self.workspace_id,
                        "run_id": new_id("activation-run"),
                        "experiment_id": experiment_id,
                        "pair_id": pair_id,
                        "case_id": case_id,
                        "side": side,
                        "mcp_server": server_name or f"shutu-{side}",
                        "mcp_server_instance_id": new_id("mcp-server"),
                        "transport": "stdio",
                    }
                    probe = self.probe_mcp_server(
                        server_command,
                        server_name=scope["mcp_server"],
                        connection_scope=scope,
                        cwd=root,
                        timeout=self.timeout,
                    )
                    runtime_hash = self._artifact_hash(root)
                    actual_runtime = runtime_hash
                    if runtime_hash != actual_prelaunch:
                        status = "MISMATCH"
                        reason = "artifact_changed_during_activation"
                    elif probe.get("initialize") and probe.get("tools_list"):
                        status = "VERIFIED"
                    else:
                        status = "UNVERIFIED"
                        reason = "mcp_handshake_incomplete"
        runtime = "unknown"
        if status in {"VERIFIED", "MISMATCH"} and actual_prelaunch:
            runtime = locals().get("actual_runtime", actual_prelaunch)
        evidence_refs = tuple(locals().get("probe", {}).get("event_refs", ()))
        connection_scope = dict(locals().get("probe", {}).get("connection_scope", {}))
        if locals().get("probe", {}).get("trace_id"):
            connection_scope["mcp_trace_id"] = locals()["probe"]["trace_id"]
        return ArtifactActivationRecord(
            experiment_id=experiment_id,
            pair_id=pair_id,
            case_id=case_id,
            side=side,
            immutable_artifact_ref=str(root) if root else "",
            capability_version_id=capability_version_id,
            expected_artifact_hash=expected_artifact_hash,
            actual_prelaunch_hash=actual_prelaunch,
            actual_runtime_hash_or_unknown=runtime,
            server_command_fingerprint=_digest(list(server_command)) if server_command else "",
            mcp_server_instance_id=str(connection_scope.get("mcp_server_instance_id") or ""),
            mcp_connection_scope=connection_scope,
            codex_home_config_fingerprint=self._config_fingerprint(
                Path(codex_home).resolve() if codex_home else Path("__missing_codex_home__")
            ),
            runner_version=self.runner_version,
            environment_fingerprint=environment_fingerprint or self.environment_fingerprint,
            activation_status=status,
            activation_evidence_refs=evidence_refs,
            activated_at=utc_now(),
            reason=reason,
        )

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
        artifact_root: str | Path | None = None,
        mcp_server_command: Sequence[str] = (),
        mcp_server_name: str = "",
        codex_home_override: str | Path | None = None,
    ) -> RealAgentRunEvidence:
        if baseline_or_candidate not in {"baseline", "candidate"}:
            raise ValueError("baseline_or_candidate must be baseline or candidate")
        if not case_id or not artifact_hash:
            raise ValueError("case_id and artifact_hash are required")
        pair_id = pair_id or new_id("pair")
        experiment_id = experiment_id or new_id("experiment")
        trace_id = new_id("real-agent")
        active_codex_home = str(Path(codex_home_override).resolve()) if codex_home_override else self.codex_home
        activation = self.activate_artifact(
            experiment_id=experiment_id,
            pair_id=pair_id,
            case_id=case_id,
            side=baseline_or_candidate,
            artifact_root=artifact_root,
            expected_artifact_hash=artifact_hash,
            capability_version_id=capability_version_id,
            codex_home=active_codex_home or None,
        )
        registration: dict[str, Any] = {}
        if activation.activation_status == "MISMATCH" or activation.activation_status == "BLOCKED_ENV":
            return self._persist(self._blocked_evidence(
                case_id=case_id,
                pair_id=pair_id,
                experiment_id=experiment_id,
                baseline_or_candidate=baseline_or_candidate,
                artifact_hash=artifact_hash,
                capability_version_id=capability_version_id,
                trace_id=trace_id,
                activation=activation,
                reason=activation.reason or "artifact_activation_failed",
            ))
        if mcp_server_command:
            if not active_codex_home:
                activation = ArtifactActivationRecord(
                    **{
                        **activation.to_dict(),
                        "activation_evidence_refs": tuple(activation.activation_evidence_refs),
                        "activation_status": "BLOCKED_ENV",
                        "reason": "isolated_codex_home_not_supplied",
                    }
                )
                return self._persist(self._blocked_evidence(
                    case_id=case_id,
                    pair_id=pair_id,
                    experiment_id=experiment_id,
                    baseline_or_candidate=baseline_or_candidate,
                    artifact_hash=artifact_hash,
                    capability_version_id=capability_version_id,
                    trace_id=trace_id,
                    activation=activation,
                    reason=activation.reason,
                ))
            registration = self.configure_mcp_server(
                mcp_server_name or f"shutu-{baseline_or_candidate}",
                mcp_server_command,
                codex_home=active_codex_home,
                timeout=self.timeout,
            )
            if not registration.get("ok"):
                activation = ArtifactActivationRecord(
                    **{
                        **activation.to_dict(),
                        "activation_evidence_refs": tuple(activation.activation_evidence_refs),
                        "activation_status": "BLOCKED_ENV",
                        "codex_home_config_fingerprint": str(registration.get("codex_home_config_fingerprint") or ""),
                        "reason": "mcp_registration_failed",
                    }
                )
                return self._persist(self._blocked_evidence(
                    case_id=case_id,
                    pair_id=pair_id,
                    experiment_id=experiment_id,
                    baseline_or_candidate=baseline_or_candidate,
                    artifact_hash=artifact_hash,
                    capability_version_id=capability_version_id,
                    trace_id=trace_id,
                    activation=activation,
                    reason=activation.reason,
                ))
            activation = self.activate_artifact(
                experiment_id=experiment_id,
                pair_id=pair_id,
                case_id=case_id,
                side=baseline_or_candidate,
                artifact_root=artifact_root,
                expected_artifact_hash=artifact_hash,
                capability_version_id=capability_version_id,
                server_command=mcp_server_command,
                server_name=mcp_server_name or f"shutu-{baseline_or_candidate}",
                codex_home=active_codex_home,
            )
            if activation.activation_status != "VERIFIED":
                return self._persist(self._blocked_evidence(
                    case_id=case_id,
                    pair_id=pair_id,
                    experiment_id=experiment_id,
                    baseline_or_candidate=baseline_or_candidate,
                    artifact_hash=artifact_hash,
                    capability_version_id=capability_version_id,
                    trace_id=trace_id,
                    activation=activation,
                    reason=activation.reason or "activation_unverified",
                ))
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
                "actual_prelaunch_hash": activation.actual_prelaunch_hash,
                "activation_status": activation.activation_status,
                "server_command_fingerprint": activation.server_command_fingerprint,
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
        if active_codex_home:
            environment["CODEX_HOME"] = active_codex_home
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
                "activation_status": activation.activation_status,
                "activation_record": activation.to_dict(),
                "execution_evidence_tier": "REAL_AGENT_OBSERVED",
                "mcp_registration": {
                    "ok": bool(registration.get("ok")) if registration else False,
                    "stdout_summary": registration.get("stdout_summary", "") if registration else "",
                    "stderr_summary": registration.get("stderr_summary", "") if registration else "",
                },
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
        codex_tool_call = any(
            event.type in {"mcp.tool_call", "mcp.tool_result"}
            or str((event.payload_json or {}).get("item_type") or "").casefold() in {"mcp_tool_call", "mcp_tool_result"}
            for event in events
        )
        execution_evidence_tier = (
            "REAL_TASK_EXECUTED"
            if codex_tool_call and activation.activation_status == "VERIFIED" and task_result in {"success", "failed"}
            else "REAL_MCP_TOOL_EXECUTED"
            if codex_tool_call and activation.activation_status == "VERIFIED"
            else "REAL_AGENT_OBSERVED"
        )
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
                "mcp_tool_calls": sum(
                    event.type in {"mcp.tool_call", "mcp.tool_result"}
                    or str((event.payload_json or {}).get("item_type") or "").casefold() in {"mcp_tool_call", "mcp_tool_result"}
                    for event in events
                ),
                "retries": sum(1 for event in events if event.retry_of_event_id or (event.attempt_index or 0) > 0),
                "tokens": None,
                "cost": None,
                "exit_code": capture.exit_code,
                "timed_out": capture.timed_out,
                "stderr_digest": _digest(capture.stderr_summary),
                "stdout_digest": _digest(capture.stdout),
                "environment_blocked": blocked_env,
                "blocked_reason": blocked_reason,
                "mcp_registration": bool(registration.get("ok")) if registration else False,
            },
            activation_status=activation.activation_status,
            activation_record=activation.to_dict(),
            execution_evidence_tier=execution_evidence_tier,
            failure_layer=blocked_reason or ("activation" if activation.activation_status != "VERIFIED" else ""),
        )
        return self._persist(evidence)

    def _blocked_evidence(
        self,
        *,
        case_id: str,
        pair_id: str,
        experiment_id: str,
        baseline_or_candidate: str,
        artifact_hash: str,
        capability_version_id: str,
        trace_id: str,
        activation: ArtifactActivationRecord,
        reason: str,
    ) -> RealAgentRunEvidence:
        manifest_hash = _digest(
            {
                "case_id": case_id,
                "pair_id": pair_id,
                "experiment_id": experiment_id,
                "side": baseline_or_candidate,
                "artifact_hash": artifact_hash,
                "activation": activation.to_dict(),
            }
        )
        return RealAgentRunEvidence(
            case_id=case_id,
            pair_id=pair_id,
            experiment_id=experiment_id,
            host="codex",
            host_version=CodexAdapter.executable_version(self.codex_command, "--version"),
            model_id=self.model_id,
            runner_id=self.runner_id,
            runner_version=self.runner_version,
            workspace_id=self.workspace_id,
            environment_fingerprint=self.environment_fingerprint,
            baseline_or_candidate=baseline_or_candidate,
            artifact_hash=artifact_hash,
            capability_version_id=capability_version_id,
            trace_ids=(),
            event_refs=activation.activation_evidence_refs,
            observed_tool_calls=(),
            missing_fields=("provider_response", "skill_lifecycle"),
            execution_status="failed" if activation.activation_status == "MISMATCH" else "blocked_env",
            task_result="unknown",
            oracle_result={"verdict": "unknown", "reason": "execution_not_started"},
            observation_completeness="partial",
            source_evidence_tier="REAL_AGENT_OBSERVED",
            session_run_digest=_digest({"trace_id": trace_id, "activation": activation.to_dict()}),
            execution_manifest_hash=manifest_hash,
            captured_at=utc_now(),
            metrics={
                "tool_calls": 0,
                "tokens": None,
                "cost": None,
                "environment_blocked": activation.activation_status != "MISMATCH",
                "blocked_reason": reason,
            },
            activation_status=activation.activation_status,
            activation_record=activation.to_dict(),
            execution_evidence_tier="REAL_ARTIFACT_ACTIVATED" if activation.activation_status == "VERIFIED" else "REAL_AGENT_OBSERVED",
            failure_layer="activation",
        )

    def run_pair(self, cases: Sequence[Mapping[str, Any]], **kwargs: Any) -> list[RealAgentRunEvidence]:
        """Run a fixed suite for both sides; Oracle stays outside the Runner."""

        results: list[RealAgentRunEvidence] = []
        pair_id = str(kwargs.pop("pair_id", "") or new_id("pair"))
        experiment_id = str(kwargs.pop("experiment_id", "") or new_id("experiment"))
        for case in cases:
            for side in ("baseline", "candidate"):
                side_kwargs = dict(kwargs)
                for name in ("artifact_root", "mcp_server_command", "mcp_server_name", "codex_home_override"):
                    side_kwargs.pop(name, None)
                artifact_root = case.get(f"{side}_artifact_root")
                command = case.get(f"{side}_mcp_server_command")
                if command is None:
                    command = case.get("mcp_server_command")
                if command is None and artifact_root:
                    root = Path(str(artifact_root)).resolve()
                    default_server = root / "server.py"
                    if default_server.is_file():
                        command = (sys.executable, str(default_server))
                side_home = ""
                if self.codex_home:
                    side_home = str(Path(self.codex_home) / experiment_id / side)
                results.append(
                    self.run(
                        case_id=str(case["case_id"]),
                        task_input=case.get("task_input"),
                        baseline_or_candidate=side,
                        artifact_hash=str(case[f"{side}_artifact_hash"]),
                        pair_id=pair_id,
                        experiment_id=experiment_id,
                        artifact_root=artifact_root,
                        mcp_server_command=tuple(str(value) for value in (command or ())),
                        mcp_server_name=f"shutu-{side}-{case['case_id']}",
                        codex_home_override=side_home or None,
                        **side_kwargs,
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
                    metrics_json, missing_fields_json, event_refs_json, payload_json,
                    activation_status, activation_record_json, execution_evidence_tier, failure_layer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    evidence.activation_status,
                    _canonical(evidence.activation_record),
                    evidence.execution_evidence_tier,
                    evidence.failure_layer,
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


def _bounded_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: str | Path | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "exit_code": completed.returncode,
            "stdout_digest": _digest(stdout),
            "stderr_digest": _digest(stderr),
            "stdout": stdout[:65536],
            "stderr": stderr[:16384],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "stdout_digest": "", "stderr_digest": "", "stdout": "", "stderr": "", "timed_out": True}
    except OSError as exc:
        return {
            "exit_code": None,
            "stdout_digest": "",
            "stderr_digest": "",
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "error_class": type(exc).__name__,
        }


def _classify_provider_result(result: Mapping[str, Any]) -> tuple[str, str, str, str]:
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".casefold()
    if result.get("timed_out"):
        return "BLOCKED_ENV", "timeout", "provider", "provider_timeout"
    if any(marker in text for marker in ("401 unauthorized", "missing bearer", "unauthorized", "authentication")):
        return "BLOCKED_ENV", "AUTHENTICATION", "provider", "provider_authentication"
    if any(marker in text for marker in ("connection refused", "could not resolve", "network", "proxy", "websocket")):
        return "BLOCKED_ENV", "NETWORK", "provider", "provider_transport"
    if result.get("exit_code") == 0:
        return "PASS", "", "", ""
    if result.get("error_class"):
        return "BLOCKED_ENV", str(result["error_class"]), "cli", "cli_unavailable"
    return "UNVERIFIED", "provider_nonzero", "provider", "provider_nonzero"


def codex_preflight(
    workspace_or_db: str | Path | M1Database,
    *,
    codex_command: str = "codex",
    codex_home: str | Path | None = None,
    cwd: str | Path | None = None,
    model_id: str = "",
    mcp_server_name: str = "",
    mcp_server_command: Sequence[str] = (),
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Run layered, redacted Codex/provider/MCP checks in an explicit HOME."""

    command = shutil.which(codex_command) or codex_command
    active_home = Path(codex_home).resolve() if codex_home else None
    if active_home:
        active_home.mkdir(parents=True, exist_ok=True)
    active_cwd = Path(cwd).resolve() if cwd else None
    environment = os.environ.copy()
    if active_home:
        environment["CODEX_HOME"] = str(active_home)
    environment["PYTHONIOENCODING"] = "utf-8"
    user_home = Path.home().resolve()
    home_scope = "isolated" if active_home and active_home not in {user_home, user_home / ".codex"} else "UNVERIFIED"
    version = CodexAdapter.executable_version(command, "--version")
    help_result = _bounded_command([command, "exec", "--help"], env=environment, cwd=active_cwd, timeout=min(timeout, 15.0))
    auth_result = _bounded_command([command, "login", "status"], env=environment, cwd=active_cwd, timeout=min(timeout, 15.0))
    auth_text = f"{auth_result.get('stdout', '')}\n{auth_result.get('stderr', '')}".casefold()
    if auth_result.get("exit_code") == 0 and not any(marker in auth_text for marker in ("not logged", "unauthorized", "missing bearer")):
        auth_status = "PASS"
    elif any(marker in auth_text for marker in ("not logged", "not authenticated", "unauthorized", "missing bearer", "login required")):
        auth_status = "NOT_AUTHENTICATED"
    elif auth_result.get("error_class"):
        auth_status = "UNVERIFIED"
    else:
        auth_status = "UNVERIFIED"
    provider_command = [
        command,
        "exec",
        "--json",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    ]
    if active_cwd:
        provider_command.extend(["--cd", str(active_cwd)])
    if model_id:
        provider_command.extend(["--model", model_id])
    provider_command.append("Return the single word READY.")
    provider_result = _bounded_command(provider_command, env=environment, cwd=active_cwd, timeout=timeout)
    provider_status, error_class, failure_layer, blocked_reason = _classify_provider_result(provider_result)
    runner = CodexRealTaskRunner(
        workspace_or_db,
        codex_command=command,
        codex_home=active_home,
        cwd=active_cwd,
        model_id=model_id,
        timeout=timeout,
    )
    registration: dict[str, Any] = {}
    mcp_probe: dict[str, Any] = {}
    if mcp_server_command and active_home:
        registration = runner.configure_mcp_server(
            mcp_server_name or "shutu-fixture",
            mcp_server_command,
            codex_home=active_home,
            timeout=timeout,
        )
        mcp_probe = runner.probe_mcp_server(
            mcp_server_command,
            server_name=mcp_server_name or "shutu-fixture",
            cwd=active_cwd,
            timeout=timeout,
        )
    elif active_home:
        mcp_list = _bounded_command([command, "mcp", "list"], env=environment, cwd=active_cwd, timeout=min(timeout, 15.0))
        registration = {"ok": mcp_list.get("exit_code") == 0, "stdout_digest": mcp_list.get("stdout_digest", ""), "stderr_digest": mcp_list.get("stderr_digest", "")}
    result: dict[str, Any] = {
        "codex_version": version,
        "cli": {
            "executable_available": version != "unknown",
            "exec_help": help_result.get("exit_code") == 0,
            "exec_help_digest": help_result.get("stdout_digest", ""),
        },
        "auth_status": auth_status,
        "provider_status": provider_status,
        "provider_kind": "codex-cli",
        "provider_version": version,
        "model_id": model_id,
        "home_scope": home_scope,
        "home_config_fingerprint": runner._config_fingerprint(active_home) if active_home else "",
        "mcp_registration": "PASS" if registration.get("ok") else "UNVERIFIED" if not registration else "BLOCKED_ENV",
        "mcp_tools_list": "PASS" if mcp_probe.get("tools_list") else "UNVERIFIED" if not mcp_probe else "BLOCKED_ENV",
        "mcp_tool_call": "PASS" if mcp_probe.get("tool_call") and mcp_probe.get("tool_result") else "UNVERIFIED" if not mcp_probe else "BLOCKED_ENV",
        "mcp_probe": {
            "trace_id": mcp_probe.get("trace_id", ""),
            "event_refs": list(mcp_probe.get("event_refs", ())),
            "request_count": (mcp_probe.get("report") or {}).get("request_count"),
            "response_count": (mcp_probe.get("report") or {}).get("response_count"),
            "tool_names": mcp_probe.get("tool_names", []),
        },
        "failure_layer": failure_layer,
        "error_class": error_class,
        "blocked_reason": blocked_reason,
        "checked_at": utc_now(),
        "evidence_digest": "",
        "raw_output_persisted": False,
        "credentials_persisted": False,
    }
    result["evidence_digest"] = _digest({key: value for key, value in result.items() if key != "evidence_digest"})
    return result


__all__ = ["ArtifactActivationRecord", "CodexRealTaskRunner", "RealAgentRunEvidence", "codex_preflight"]
