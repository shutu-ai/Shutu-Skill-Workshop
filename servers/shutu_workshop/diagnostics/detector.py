"""Deterministic, evidence-gated M3 rules.

This module only reads a bounded ``DetectorContext``.  It never writes the
database, calls a model, or treats a missing observation as a fact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Iterable

from ..models import TraceEvent, new_id, utc_now
from .models import (
    AttributionHypothesis,
    DetectorContext,
    EvidenceRef,
    RuleFinding,
    RuleSpec,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any, length: int = 24) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _payload(event: TraceEvent) -> dict[str, Any]:
    value = event.payload_json if isinstance(event.payload_json, dict) else {}
    nested = value.get("payload")
    return nested if isinstance(nested, dict) else value


def _result(event: TraceEvent) -> dict[str, Any]:
    value = _payload(event).get("result")
    return value if isinstance(value, dict) else {}


def _error(event: TraceEvent) -> dict[str, Any]:
    value = _payload(event).get("error")
    return value if isinstance(value, dict) else {}


def _scope(event: TraceEvent) -> dict[str, str]:
    value = event.source_metadata.get("correlation_scope", {})
    if not isinstance(value, dict):
        value = {}
    allowed = (
        "workspace_id",
        "run_id",
        "agent_session_id",
        "mcp_server",
        "mcp_connection",
        "mcp_session",
        "transport",
        "request_id",
    )
    result = {name: _text(value.get(name, "")) for name in allowed}
    result["request_id"] = event.request_id or result["request_id"]
    return {key: value for key, value in result.items() if value}


def _http_status(event: TraceEvent) -> int | None:
    payload = _payload(event)
    metadata = event.source_metadata
    for value in (
        payload.get("http_status"),
        payload.get("status_code"),
        metadata.get("http_status"),
        metadata.get("status_code"),
    ):
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    error_type = event.error_type.casefold()
    if error_type.startswith("http."):
        try:
            return int(error_type.split(".", 1)[1])
        except ValueError:
            pass
    return None


def _error_code(event: TraceEvent) -> str:
    error = _error(event)
    payload = _payload(event)
    value = error.get("code") or payload.get("error_code") or payload.get("code")
    if value not in (None, ""):
        return _text(value)[:80]
    return ""


def _tool_name(event: TraceEvent) -> str:
    payload = _payload(event)
    return event.tool_name or _text(payload.get("tool_name") or payload.get("name")) or "unknown"


def _is_mcp_error(event: TraceEvent) -> bool:
    result = _result(event)
    payload = _payload(event)
    return (
        bool(result.get("isError") is True)
        or bool(payload.get("isError") is True)
        or bool(payload.get("is_error") is True)
        or bool(_error(event))
        or event.error_type == "mcp.error"
    ) and event.type.casefold() in {
        "mcp.tool_result",
        "mcp.tool_result.received",
        "mcp.response",
        "tool.result",
        "tool_result",
    }


def _transport_class(event: TraceEvent) -> tuple[str, str, str]:
    """Return category, owner kind, and stable error label."""

    payload = _payload(event)
    raw_error = payload.get("error")
    error_type = f"{event.error_type} {_text(raw_error)}".casefold()
    status = _http_status(event)
    if not (
        event.error_type
        or status is not None
        or event.status.casefold() in {"error", "failed", "timeout", "timed_out"}
        or raw_error not in (None, "", {})
    ):
        return "", "", ""
    metadata = event.source_metadata
    layer = _text(metadata.get("layer") or metadata.get("failure_layer") or metadata.get("relay_layer")).casefold()
    if (
        layer in {"observer", "relay", "observation"}
        or "relay" in error_type
        or "observer" in error_type
        or "relay" in event.transport.casefold()
    ):
        return "observer_failure_observed", "observer", f"observer:{status or error_type or 'failure'}"
    if status in {401, 403} or any(value in error_type for value in ("auth", "unauthorized", "forbidden", "permission")):
        return "auth_failure_observed", "auth", f"auth:{status or error_type}"
    if status is not None and status >= 500:
        return "network_failure_observed", "network", f"http:{status}"
    if any(value in error_type for value in ("timeout", "timed_out", "connection", "network", "dns", "transport")):
        return "network_failure_observed", "network", error_type[:96]
    return "", "", ""


def _owner(
    owner_kind: str,
    *,
    capability_id: str = "",
    basis: str = "explicit_error",
    confidence: str = "moderate_evidence",
    verification: tuple[str, ...] = (),
) -> AttributionHypothesis:
    return AttributionHypothesis(
        owner_kind=owner_kind or "unknown",
        owner_capability_id=capability_id,
        state="hypothesis",
        basis=basis,
        confidence_grade=confidence,
        contributing_capabilities=(capability_id,) if capability_id else (),
        required_verification=verification,
    )


def _finding(
    context: DetectorContext,
    spec: RuleSpec,
    events: Iterable[TraceEvent],
    *,
    signature: str,
    category: str,
    severity: str,
    observed_fact: dict[str, Any],
    owner: AttributionHypothesis,
    unknown_fields: tuple[str, ...] = (),
    confidence: str = "high_evidence",
    attribution_state: str = "hypothesis",
    error_fingerprint: str = "",
) -> RuleFinding:
    evidence_events = tuple(events)
    refs = tuple(
        EvidenceRef(
            trace_id=context.trace.trace_id,
            event_id=event.event_id,
            capability_version_id=event.capability_version_id,
            correlation_scope=_scope(event),
        )
        for event in evidence_events
    )
    first = evidence_events[0] if evidence_events else None
    capability_id = first.capability_id if first else ""
    capability_version_id = first.capability_version_id if first else ""
    source_scope = _scope(first) if first else {}
    identity = {
        "trace_id": context.trace.trace_id,
        "rule_id": spec.rule_id,
        "rule_version": spec.rule_version,
        "category": category,
        "signature": signature,
        "events": [event.event_id for event in evidence_events],
    }
    return RuleFinding(
        finding_id=f"finding_{_digest(identity, 32)}",
        rule_id=spec.rule_id,
        rule_version=spec.rule_version,
        signature=signature,
        category=category,
        severity=severity,
        observed_fact=observed_fact,
        evidence_refs=refs,
        owner_candidates=(owner,),
        attribution_state=attribution_state,
        confidence_or_evidence_grade=confidence,
        unknown_fields=unknown_fields,
        detected_at=(first.occurred_at or utc_now()) if first else utc_now(),
        detector_version="",
        trace_id=context.trace.trace_id,
        capability_id=capability_id,
        capability_version_id=capability_version_id,
        source_scope=source_scope,
        error_fingerprint=error_fingerprint,
    )


RuleFunction = Callable[[DetectorContext, RuleSpec], list[RuleFinding]]


class RuleRegistry:
    """Small versioned registry with a stable fingerprint."""

    def __init__(self, specs: Iterable[RuleSpec] | None = None) -> None:
        self._specs: dict[str, RuleSpec] = {}
        for spec in specs or default_rule_specs():
            self.register(spec)

    def register(self, spec: RuleSpec) -> RuleSpec:
        if not spec.rule_id:
            raise ValueError("rule_id is required")
        self._specs[spec.rule_id] = spec
        return spec

    def get(self, rule_id: str) -> RuleSpec | None:
        return self._specs.get(rule_id)

    def list(self) -> list[RuleSpec]:
        return [self._specs[key] for key in sorted(self._specs)]

    def fingerprint(self) -> str:
        return _digest([spec.to_dict() for spec in self.list()], 64)


def default_rule_specs() -> tuple[RuleSpec, ...]:
    return (
        RuleSpec("R01", "1", ("type", "payload.is_error_or_jsonrpc_error", "event_id"), "event", {"default": "error"}),
        RuleSpec("R02", "1", ("error_type_or_http_status", "event_id"), "event", {"default": "error"}),
        RuleSpec("R03", "1", ("schema_hash_or_version", "validation_result", "event_id"), "event", {"default": "warning"}),
        RuleSpec("R04", "1", ("retry_of_event_id_or_attempt_index", "event_id"), "trace", {"default": "warning"}),
        RuleSpec("R05", "1", ("observation_completeness_or_gap", "trace_id"), "trace", {"default": "warning"}),
        RuleSpec("R06", "1", ("expected_capability_id", "trigger_expectation", "observation_scope"), "trace", {"default": "warning"}),
    )


class RuleDetector:
    detector_version = "m3-deterministic-2"

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self.registry = registry or RuleRegistry()

    def ruleset_fingerprint(self) -> str:
        return _digest(
            {"detector_version": self.detector_version, "rules": self.registry.fingerprint()},
            64,
        )

    def detect(self, context: DetectorContext) -> list[RuleFinding]:
        events = tuple(sorted(context.ordered_events, key=lambda item: (item.seq, item.event_id)))
        context = replace(context, ordered_events=events)
        findings: list[RuleFinding] = []
        for spec in self.registry.list():
            if not spec.enabled:
                continue
            detector = getattr(self, f"_detect_{spec.rule_id.lower()}", None)
            if detector is None:
                continue
            findings.extend(detector(context, spec))
        return [replace(item, detector_version=self.detector_version) for item in findings]

    def _detect_r01(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for event in context.ordered_events:
            if not _is_mcp_error(event):
                continue
            code = _error_code(event)
            scope = _scope(event)
            server = scope.get("mcp_server", "")
            label = code or event.error_type or "mcp_business_error"
            fact = {
                "kind": "mcp_business_error",
                "tool_name": _tool_name(event),
                "request_id": event.request_id or "not_observed",
                "error_code": code or "not_observed",
                "is_error": True,
                "transport": event.transport or "not_observed",
                "server": server or "not_observed",
            }
            unknown = tuple(
                value
                for value, missing in (
                    ("capability_version_id", not bool(event.capability_version_id)),
                    ("mcp_server", not bool(server)),
                    ("jsonrpc_error_code", not bool(code)),
                )
                if missing
            )
            findings.append(
                _finding(
                    context,
                    spec,
                    [event],
                    signature=f"tool_failure:{_tool_name(event)}:{label}",
                    category="tool_failure_observed",
                    severity=spec.severity_policy.get("default", "error"),
                    observed_fact=fact,
                    owner=_owner(
                        "mcp_server" if server else "unknown",
                        capability_id=event.capability_id if server else "",
                        verification=("inspect server business semantics",),
                    ),
                    unknown_fields=unknown,
                    error_fingerprint=_digest({"category": "mcp", "tool": _tool_name(event), "label": label}),
                )
            )
        return findings

    def _detect_r02(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for event in context.ordered_events:
            category, owner_kind, label = _transport_class(event)
            if not category or _is_mcp_error(event):
                continue
            status = _http_status(event)
            fact = {
                "kind": category,
                "request_id": event.request_id or "not_observed",
                "error_type": event.error_type or "not_observed",
                "http_status": status if status is not None else "not_observed",
                "transport": event.transport or "not_observed",
                "source": event.observation_source or "not_observed",
            }
            findings.append(
                _finding(
                    context,
                    spec,
                    [event],
                    signature=f"{category}:{label}",
                    category=category,
                    severity=spec.severity_policy.get("default", "error"),
                    observed_fact=fact,
                    owner=_owner(
                        owner_kind,
                        basis="observer_failure" if owner_kind == "observer" else "explicit_error",
                        confidence="high_evidence",
                        verification=("verify upstream or host boundary",),
                    ),
                    unknown_fields=("capability_version_id",) if not event.capability_version_id else (),
                    error_fingerprint=_digest({"category": category, "label": label}),
                )
            )
        return findings

    def _detect_r03(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        findings: list[RuleFinding] = []
        for event in context.ordered_events:
            payload = _payload(event)
            validation = payload.get("validation")
            if not isinstance(validation, dict):
                validation = payload.get("schema_validation")
            if not isinstance(validation, dict):
                validation = {}
            schema_hash = _text(validation.get("schema_hash") or payload.get("schema_hash"))
            schema_version = _text(validation.get("schema_version") or payload.get("schema_version"))
            valid = validation.get("valid")
            if valid is None:
                valid = validation.get("ok")
            invalid = valid is False or _text(validation.get("result")).casefold() in {"invalid", "failed", "mismatch"}
            explicit_error = "schema" in _text(payload.get("error")).casefold() and bool(_error(event))
            if not (invalid or explicit_error):
                argument_keys = payload.get("argument_keys")
                if isinstance(argument_keys, list) and event.type.casefold() in {"mcp.tool_call", "tool.call", "tool.before"}:
                    findings.append(
                        _finding(
                            context,
                            spec,
                            [event],
                            signature=f"schema_evidence_missing:{_tool_name(event)}",
                            category="unsupported_or_unknown",
                            severity="info",
                            observed_fact={
                                "kind": "schema_validation_insufficient_evidence",
                                "argument_key_count": len(argument_keys),
                                "schema_hash": "not_observed",
                                "validation_result": "not_observed",
                            },
                            owner=_owner("unknown", basis="schema_violation", confidence="unknown", verification=("capture schema snapshot and validation result",)),
                            unknown_fields=("schema_hash", "schema_version", "validation_result"),
                            confidence="unknown",
                            attribution_state="insufficient_evidence",
                            error_fingerprint=_digest({"category": "schema", "kind": "insufficient"}),
                        )
                    )
                continue
            if not (schema_hash or schema_version):
                continue
            fact = {
                "kind": "schema_validation_failure",
                "schema_hash": schema_hash or "not_observed",
                "schema_version": schema_version or "not_observed",
                "validation": "invalid",
                "violation_count": len(validation.get("violations", [])) if isinstance(validation.get("violations"), list) else "not_observed",
            }
            findings.append(
                _finding(
                    context,
                    spec,
                    [event],
                    signature=f"schema_mismatch:{schema_hash or schema_version}",
                    category="schema_mismatch_observed",
                    severity=spec.severity_policy.get("default", "warning"),
                    observed_fact=fact,
                    owner=_owner(
                        "agent_decision",
                        capability_id=event.capability_id,
                        basis="schema_violation",
                        confidence="high_evidence",
                        verification=("review task guidance or agent argument construction",),
                    ),
                    unknown_fields=("violation_paths",),
                    error_fingerprint=_digest({"category": "schema", "schema": schema_hash or schema_version}),
                )
            )
        return findings

    def _detect_r04(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        by_id = {event.event_id: event for event in context.ordered_events}
        findings: list[RuleFinding] = []
        for event in context.ordered_events:
            if not event.retry_of_event_id or event.retry_of_event_id not in by_id:
                continue
            original = by_id[event.retry_of_event_id]
            if not (_is_mcp_error(original) or _transport_class(original)[0] or original.status in {"error", "failed"}):
                continue
            if not (_is_mcp_error(event) or _transport_class(event)[0] or event.status in {"error", "failed"}):
                continue
            scope = _scope(event)
            same_source = event.observation_source == original.observation_source
            same_connection = not scope.get("mcp_connection") or scope.get("mcp_connection") == _scope(original).get("mcp_connection")
            if not (same_source and same_connection):
                continue
            label = event.error_type or _error_code(event) or "same_failure"
            findings.append(
                _finding(
                    context,
                    spec,
                    [original, event],
                    signature=f"retry_failure:{_tool_name(event)}:{label}",
                    category="retry_failure_pattern",
                    severity=spec.severity_policy.get("default", "warning"),
                    observed_fact={
                        "kind": "explicit_retry_failure",
                        "retry_of_event_id": original.event_id,
                        "attempt_index": event.attempt_index if event.attempt_index is not None else "not_observed",
                        "tool_name": _tool_name(event),
                    },
                    owner=_owner("unknown", basis="retry_chain", confidence="moderate_evidence", verification=("verify environment change between attempts",)),
                    unknown_fields=("environment_change",),
                    confidence="moderate_evidence",
                    error_fingerprint=_digest({"category": "retry", "tool": _tool_name(event), "label": label}),
                )
            )
        return findings

    def _detect_r05(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        trace = context.trace
        events = context.ordered_events
        gaps: list[TraceEvent] = []
        completed_keys = {
            (event.observation_source, event.request_id, event.tool_name)
            for event in events
            if event.type.casefold() in {"tool.result", "mcp.tool_result", "tool.after", "tool_result"}
        }
        for event in events:
            if event.type.casefold() not in {"tool.started", "mcp.tool_call", "tool.before", "tool.call"}:
                continue
            key = (event.observation_source, event.request_id, event.tool_name)
            if event.request_id and key not in completed_keys:
                gaps.append(event)
        metadata = trace.metadata if isinstance(trace.metadata, dict) else {}
        explicit_gap = any(
            key in metadata for key in ("failed_observations", "incomplete_lines", "observation_gap", "capture_error")
        )
        explicit_gap = explicit_gap or any(
            any(key in event.source_metadata for key in ("failed_observations", "incomplete_lines", "observation_gap"))
            for event in events
        )
        closed = trace.status != "running"
        if not closed or (not gaps and not explicit_gap and trace.observation_completeness not in {"partial", "incomplete"}):
            return []
        evidence = gaps[:3] or list(events[-1:])
        if not evidence:
            return []
        return [
            _finding(
                context,
                spec,
                evidence,
                signature="observation_gap:" + ("missing_response" if gaps else trace.observation_completeness),
                category="incomplete_observation",
                severity=spec.severity_policy.get("default", "warning"),
                observed_fact={
                    "kind": "telemetry_gap",
                    "trace_status": trace.status,
                    "observation_completeness": trace.observation_completeness,
                    "missing_response_count": len(gaps),
                },
                owner=_owner("observer", basis="observer_failure", confidence="moderate_evidence", verification=("inspect capture/observer report",)),
                unknown_fields=("missing_events", "task_result") if trace.task_result == "unknown" else ("missing_events",),
                confidence="moderate_evidence",
                error_fingerprint=_digest({"category": "observation_gap", "kind": "missing_response" if gaps else trace.observation_completeness}),
            )
        ]

    def _detect_r06(self, context: DetectorContext, spec: RuleSpec) -> list[RuleFinding]:
        metadata = context.trace.metadata if isinstance(context.trace.metadata, dict) else {}
        expected = _text(metadata.get("expected_capability_id"))
        required = (
            metadata.get("expected_capability_discoverable") is True,
            metadata.get("trigger_conditions_met") is True,
            metadata.get("observation_scope_sufficient") is True,
        )
        if not expected:
            return []
        if not all(required):
            evidence = list(context.ordered_events[-1:])
            if not evidence:
                return []
            unknown = tuple(
                name
                for name, present in (
                    ("expected_capability_discoverable", required[0]),
                    ("trigger_conditions_met", required[1]),
                    ("observation_scope_sufficient", required[2]),
                )
                if not present
            )
            return [
                _finding(
                    context,
                    spec,
                    evidence,
                    signature=f"skill_expectation_not_assessable:{expected}",
                    category="not_assessable",
                    severity="info",
                    observed_fact={"kind": "missing_expectation_evidence", "expected_capability_id": expected},
                    owner=_owner("unknown", basis="task_expectation", confidence="unknown", verification=("provide discoverability, trigger, and observation evidence",)),
                    unknown_fields=unknown,
                    confidence="unknown",
                    attribution_state="insufficient_evidence",
                    error_fingerprint=_digest({"category": "missing_trigger", "kind": "not_assessable"}),
                )
            ]
        observed = any(event.capability_id == expected for event in context.ordered_events)
        if observed:
            return []
        evidence = list(context.ordered_events[-1:])
        if not evidence:
            return []
        return [
            _finding(
                context,
                spec,
                evidence,
                signature=f"skill_missing_trigger:{expected}",
                category="skill_missing_trigger",
                severity=spec.severity_policy.get("default", "warning"),
                observed_fact={
                    "kind": "expected_capability_not_observed",
                    "expected_capability_id": expected,
                    "expectation_source": "explicit_trace_metadata",
                },
                owner=_owner("skill", capability_id=expected, basis="task_expectation", confidence="moderate_evidence", verification=("review capability discovery and trigger condition evidence",)),
                unknown_fields=("skill_lifecycle",),
                confidence="moderate_evidence",
                error_fingerprint=_digest({"category": "missing_trigger", "capability": expected}),
            )
        ]


def rule_specs() -> list[RuleSpec]:
    return list(default_rule_specs())


__all__ = [
    "RuleDetector",
    "RuleRegistry",
    "RuleSpec",
    "default_rule_specs",
    "rule_specs",
]
