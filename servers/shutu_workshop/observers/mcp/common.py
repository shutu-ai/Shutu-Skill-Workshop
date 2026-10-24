"""MCP observation summaries and the shared M1 trace writer."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
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
        self._seq = 0
        self._started = False
        self._closed = False
        self._pending: dict[str, tuple[str, str, float]] = {}
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
            if direction == "client_to_server" and "method" in message:
                self.report.request_count += 1
                if request_key:
                    if request_key in self._pending:
                        self.report.duplicate_requests += 1
                    self._pending[request_key] = (method, str(summary.get("tool_name", "")), time.perf_counter())
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
                direction, message, request_key, response_for
            )
            if status_code is not None and status_code >= 400:
                status = "error"
                error_type = f"http.{status_code}"
            metadata = {
                "direction": direction,
                "observation_source": "mcp_observer",
                "protocol_era": self.report.protocol_era,
                "headers": _safe_headers(headers or {}),
            }
            event_seq = self._next_seq()
            event = TraceEvent(
                trace_id=self.trace_id,
                seq=event_seq,
                type=event_type,
                actor="client" if direction == "client_to_server" else "server",
                event_id=f"{self.adapter_id}:{direction}:{event_seq}",
                occurred_at=utc_now(),
                duration_ms=self._response_duration(request_id),
                capability_id=self._tool_capability(tool_name),
                capability_version_id=self._tool_versions.get(tool_name, ""),
                adapter_event_id=f"{direction}:{request_id or event_seq}",
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
                self._pending.pop(request_id, None)
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
        request_key: str,
        response_for: str,
    ) -> tuple[str, str, str, str, str]:
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        method = str(message.get("method", ""))
        tool_name = str(params.get("name", "") or params.get("tool", ""))
        key = request_key or response_for
        pending = self._pending.get(key, ("", "", 0.0))
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

    def _response_duration(self, request_id: str) -> int | None:
        if not request_id:
            return None
        pending = self._pending.get(request_id)
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
    """Group only events with explicit request/call IDs; unknowns stay separate."""

    groups: dict[str, list[TraceEvent]] = {}
    result: list[list[TraceEvent]] = []
    for event in events:
        key = event.request_id or event.event_id
        if event.request_id:
            groups.setdefault(key, []).append(event)
        else:
            result.append([event])
    result.extend(groups.values())
    return result
