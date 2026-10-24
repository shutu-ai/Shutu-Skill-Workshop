"""MCP observation summaries and the shared M1 trace writer."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Iterable, Mapping

from ...capabilities import CapabilityRegistry, content_hash
from ...ingestion import TraceIngestor
from ...models import Trace, TraceEvent, new_id, utc_now
from ...redaction import RedactionPolicy
from .protocol_versions import MODERN, detect_era, protocol_label


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def jsonrpc_id(message: Mapping[str, Any]) -> str:
    if "id" not in message:
        return ""
    return _canonical(message["id"])


def _safe_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    secret_names = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
    return {
        "present": sorted(str(name).casefold() for name in headers),
        "session_header_present": any(str(name).casefold() == "mcp-session-id" for name in headers),
        "credentials_present": any(str(name).casefold() in secret_names for name in headers),
    }


def message_summary(message: Mapping[str, Any], policy: RedactionPolicy) -> dict[str, Any]:
    method = str(message.get("method", ""))
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    error = message.get("error") if isinstance(message.get("error"), dict) else {}
    tool_name = str(params.get("name", "") or params.get("tool", ""))
    summary: dict[str, Any] = {
        "jsonrpc": message.get("jsonrpc", ""),
        "id_present": "id" in message,
        "id": message.get("id", "") if "id" in message else "",
        "method": method,
        "tool_name": tool_name,
        "argument_keys": sorted(params.get("arguments", {}).keys()) if isinstance(params.get("arguments"), dict) else [],
        "result_keys": sorted(result.keys()),
        "error_code": error.get("code", "") if error else "",
        "is_error": bool(error) or bool(result.get("isError", False)),
    }
    if method in {"tools/list", "server/discover"}:
        if isinstance(result.get("tools"), list):
            summary["tool_count"] = len(result["tools"])
            summary["schema_hash"] = content_hash(result["tools"])
        summary["server_info"] = result.get("serverInfo", {})
    # Redact nested metadata while keeping the event bounded and explainable.
    return policy.redact_payload(summary)


@dataclass(frozen=True)
class CorrelationScope:
    """Names the namespace in which a request ID is meaningful.

    JSON-RPC IDs are only locally unique.  Keeping the scope in source metadata
    lets callers preserve uncertain cross-source links without treating a
    repeated integer as proof of one shared call.
    """

    workspace_id: str = ""
    run_id: str = ""
    agent_session_id: str = ""
    mcp_server: str = ""
    mcp_connection: str = ""
    mcp_session: str = ""
    transport: str = ""
    request_id: str = ""

    def without_request(self) -> tuple[str, ...]:
        return (
            self.workspace_id,
            self.run_id,
            self.agent_session_id,
            self.mcp_server,
            self.mcp_connection,
            self.mcp_session,
            self.transport,
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_event(cls, event: TraceEvent) -> "CorrelationScope":
        value = event.source_metadata.get("correlation_scope", {})
        if not isinstance(value, dict):
            return cls(request_id=event.request_id)
        names = {field.name for field in fields(cls)}
        values = {name: str(value.get(name, "")) for name in names}
        values["request_id"] = event.request_id or values["request_id"]
        return cls(**values)

    def can_group_within_source(self, event: TraceEvent) -> bool:
        if not self.request_id:
            return False
        if event.observation_source == "mcp_observer":
            return bool(self.mcp_server and self.mcp_connection and self.transport)
        if event.observation_source == "agent_adapter":
            return bool(self.run_id or self.agent_session_id)
        return bool(self.run_id or self.mcp_connection)

def _session_fingerprint(headers: Mapping[str, str]) -> str:
    for name, value in headers.items():
        if str(name).casefold() == "mcp-session-id" and value:
            return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]
    return ""


@dataclass
class ObserverReport:
    transport: str
    protocol_era: str = "unknown"
    forwarded_messages: int = 0
    observed_messages: int = 0
    request_count: int = 0
    response_count: int = 0
    notification_count: int = 0
    failed_observations: int = 0
    duplicate_requests: int = 0
    process_exit_code: int | None = None
    timed_out: bool = False
    observer_overhead_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    stderr_summary: str = ""
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class McpTraceRecorder:
    """Fail-open trace recording shared by stdio and HTTP observers."""

    def __init__(
        self,
        ingestor: TraceIngestor | None = None,
        *,
        registry: CapabilityRegistry | None = None,
        workspace_id: str = "local",
        trace_id: str = "",
        adapter_id: str = "mcp-observer",
        adapter_version: str = "1",
        server_name: str = "test-server",
        transport: str = "",
        policy: RedactionPolicy | None = None,
    ) -> None:
        self.ingestor = ingestor
        self.registry = registry or (CapabilityRegistry(ingestor.db) if ingestor else None)
        self.workspace_id = workspace_id
        self.trace_id = trace_id or new_id("mcp-trace")
        self.adapter_id = adapter_id
        self.adapter_version = adapter_version
        self.server_name = server_name
        self.transport = transport
        self.policy = policy or RedactionPolicy()
        self.report = ObserverReport(transport=transport, trace_id=self.trace_id)
        self._lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._seq = 0
        self._started = False
        self._closed = False
        self._pending: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._tool_versions: dict[str, str] = {}

    def start(self) -> None:
        if self._started or not self.ingestor:
            self._started = True
            return
        trace = Trace(
            trace_id=self.trace_id,
            workspace_id=self.workspace_id,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            source=f"mcp.{self.transport}",
            observation_completeness="partial",
            metadata={
                "server_name": self.server_name,
                "transport": self.transport,
                "protocol_era": self.report.protocol_era,
                "correlation_scope": {
                    "workspace_id": self.workspace_id,
                    "run_id": self.trace_id,
                    "mcp_server": self.server_name,
                    "mcp_connection": self.trace_id,
                    "transport": self.transport,
                },
            },
        )
        try:
            self.ingestor.start(trace)
        except Exception as exc:
            # A duplicate start is safe; other failures must not affect forwarding.
            if self.ingestor.traces.get(self.trace_id) is None:
                self._record_failure(exc)
        self._started = True

    def observe_json(
        self,
        direction: str,
        message: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        status_code: int | None = None,
        response_for: str = "",
        connection_id: str = "",
        agent_session_id: str = "",
        correlation_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        self.start()
        started = time.perf_counter_ns()
        try:
            summary = message_summary(message, self.policy)
            request_key = jsonrpc_id(message)
            method = str(message.get("method", ""))
            is_response = "result" in message or "error" in message
            if is_response and not request_key:
                request_key = response_for
            connection_id = connection_id or self.trace_id
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
            agent_session_id = agent_session_id or str(meta.get("ai.opencode/sessionID", ""))
            pending_key = (connection_id, request_key)
            if direction == "client_to_server" and "method" in message:
                self.report.request_count += 1
                if request_key:
                    with self._pending_lock:
                        if pending_key in self._pending:
                            self.report.duplicate_requests += 1
                        self._pending[pending_key] = (
                            method,
                            str(summary.get("tool_name", "")),
                            time.perf_counter(),
                        )
            elif direction == "server_to_client" and is_response:
                self.report.response_count += 1
            elif "method" in message and "id" not in message:
                self.report.notification_count += 1
            detected_era = detect_era(message, headers)
            if detected_era != "unknown":
                self.report.protocol_era = detected_era
            if self.report.protocol_era != "unknown":
                summary["protocol"] = protocol_label(self.report.protocol_era)
            event_type, tool_name, status, error_type, request_id = self._event_fields(
                direction, message, pending_key
            )
            if status_code is not None and status_code >= 400:
                status = "error"
                error_type = f"http.{status_code}"
            metadata = {
                "direction": direction,
                "observation_source": "mcp_observer",
                "protocol_era": self.report.protocol_era,
                "headers": _safe_headers(headers or {}),
                "correlation_scope": CorrelationScope(
                    workspace_id=self.workspace_id,
                    run_id=self.trace_id,
                    agent_session_id=agent_session_id,
                    mcp_server=self.server_name,
                    mcp_connection=connection_id,
                    mcp_session=_session_fingerprint(headers or {}),
                    transport=self.transport,
                    request_id=request_id,
                ).to_dict(),
            }
            if correlation_evidence:
                metadata["correlation_evidence"] = dict(correlation_evidence)
            event_seq = self._next_seq()
            event = TraceEvent(
                trace_id=self.trace_id,
                seq=event_seq,
                type=event_type,
                actor="client" if direction == "client_to_server" else "server",
                event_id=f"{self.adapter_id}:{direction}:{event_seq}",
                occurred_at=utc_now(),
                duration_ms=self._response_duration(pending_key),
                capability_id=self._tool_capability(tool_name),
                capability_version_id=self._tool_versions.get(tool_name, ""),
                adapter_event_id=f"{connection_id}:{direction}:{request_id or event_seq}",
                request_id=request_id,
                tool_name=tool_name,
                transport=self.transport,
                observation_source="mcp_observer",
                status=status,
                error_type=error_type,
                correlation_quality="exact" if request_id else "unknown",
                payload_json=summary,
                source_metadata=metadata,
            )
            self._write(event)
            self.report.observed_messages += 1
            if direction == "server_to_client" and request_id:
                with self._pending_lock:
                    self._pending.pop(pending_key, None)
            if direction == "server_to_client":
                result = message.get("result") if isinstance(message.get("result"), dict) else {}
                if isinstance(result.get("tools"), list):
                    self.register_tools(
                        result["tools"],
                        server_info=result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {},
                        protocol=self.report.protocol_era,
                    )
        except Exception as exc:  # fail-open: never replay or block a request
            self._record_failure(exc)
        finally:
            self.report.observer_overhead_ms += (time.perf_counter_ns() - started) / 1_000_000

    def register_tools(
        self,
        tools: list[dict[str, Any]],
        *,
        server_info: dict[str, Any] | None = None,
        protocol: str = "",
    ) -> None:
        if not self.registry:
            return
        try:
            server_version = "mcp-server-" + content_hash(
                {"server_info": server_info or {}, "protocol": protocol, "tools": tools}
            )[:24]
            self.registry.register_mcp_server_snapshot(
                self.server_name,
                server_info=server_info or {},
                protocol=protocol,
                transport=self.transport,
                tools=tools,
                version_id=server_version,
                adapter_id=self.adapter_id,
            )
            for tool in tools:
                name = str(tool.get("name", ""))
                if not name:
                    continue
                version_id = "mcp-tool-" + content_hash(tool)[:24]
                value = self.registry.register_mcp_tool_snapshot(
                    self.server_name,
                    tool,
                    provider_version=str((server_info or {}).get("version") or "unknown"),
                    version_id=version_id,
                    adapter_id=self.adapter_id,
                )
                self._tool_versions[name] = value.version_id
        except Exception as exc:
            self._record_failure(exc)

    def close(self, *, status: str = "incomplete") -> ObserverReport:
        if self._closed:
            return self.report
        if self.ingestor and self._started and self.ingestor.traces.get(self.trace_id):
            try:
                self.ingestor.close(
                    self.trace_id,
                    status if status in Trace.STATUSES else "incomplete",
                    task_result="unknown",
                    observation_completeness="partial",
                )
            except Exception as exc:
                self._record_failure(exc)
        self._closed = True
        return self.report

    def _event_fields(
        self,
        direction: str,
        message: Mapping[str, Any],
        pending_key: tuple[str, str],
    ) -> tuple[str, str, str, str, str]:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        method = str(message.get("method", ""))
        tool_name = str(params.get("name", "") or params.get("tool", ""))
        key = pending_key[1]
        with self._pending_lock:
            pending = self._pending.get(pending_key, ("", "", 0.0))
        effective_method = method or pending[0]
        tool_name = tool_name or pending[1]
        if effective_method == "initialize":
            event_type = "mcp.initialize"
        elif effective_method == "server/discover":
            event_type = "mcp.server_discover"
        elif effective_method == "tools/list":
            event_type = "mcp.tools_list"
        elif effective_method == "tools/call":
            event_type = "mcp.tool_call" if direction == "client_to_server" else "mcp.tool_result"
        elif effective_method in {"$/cancelRequest", "$ /cancelRequest"}:
            event_type = "mcp.cancel"
        elif effective_method:
            event_type = "mcp.notification" if "id" not in message else "mcp.server_request"
        elif error:
            event_type = "mcp.error"
        else:
            event_type = "mcp.response"
        status = "error" if error or bool(result.get("isError", False)) else ("started" if method else "ok")
        error_type = "mcp.error" if error or bool(result.get("isError", False)) else ""
        return event_type, tool_name, status, error_type, key

    def _response_duration(self, pending_key: tuple[str, str]) -> int | None:
        if not pending_key[1]:
            return None
        with self._pending_lock:
            pending = self._pending.get(pending_key)
        if not pending:
            return None
        return round((time.perf_counter() - pending[2]) * 1000)

    def _tool_capability(self, tool_name: str) -> str:
        if not tool_name or tool_name not in self._tool_versions:
            return ""
        return f"mcp-tool:{self.server_name}/{tool_name}"

    def _next_seq(self) -> int:
        with self._lock:
            value = self._seq
            self._seq += 1
            return value

    def _write(self, event: TraceEvent) -> None:
        if not self.ingestor:
            return
        self.ingestor.append(event)

    def _record_failure(self, exc: BaseException) -> None:
        self.report.failed_observations += 1
        message = type(exc).__name__
        if message not in self.report.errors:
            self.report.errors.append(message)


def correlate_events(events: Iterable[TraceEvent]) -> list[list[TraceEvent]]:
    """Group only scoped calls or calls with an explicit verified bridge.

    A bare JSON-RPC ID is intentionally never enough to join Agent and MCP
    observations.  Internal request/response pairs may group within their
    confirmed source scope; cross-source groups require the same verified
    ``correlation_evidence.bridge_id``.
    """

    groups: dict[tuple[str, ...], list[TraceEvent]] = {}
    result: list[list[TraceEvent]] = []
    for event in events:
        if not event.request_id:
            result.append([event])
            continue
        scope = CorrelationScope.from_event(event)
        evidence = event.source_metadata.get("correlation_evidence", {})
        bridge_id = str(evidence.get("bridge_id", "")) if isinstance(evidence, dict) else ""
        verified = isinstance(evidence, dict) and evidence.get("verified") is True
        if verified and bridge_id:
            key = ("bridge", bridge_id)
        elif scope.can_group_within_source(event):
            key = (
                "internal",
                event.observation_source,
                *scope.without_request(),
                event.request_id,
            )
        else:
            result.append([event])
            continue
        groups.setdefault(key, []).append(event)
    result.extend(groups.values())
    return result
