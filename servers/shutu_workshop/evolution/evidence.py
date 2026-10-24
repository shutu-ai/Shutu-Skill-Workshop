"""Bounded, reference-checked input bundles for M4 diagnosis."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..capabilities import CapabilityRegistry
from ..diagnostics import DiagnosisService
from ..models import TraceEvent
from ..redaction import RedactionPolicy
from ..storage import M1Database
from ..traces import TraceStore
from .models import EvidenceBundle


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class EvidenceValidationError(ValueError):
    pass


def _event_summary(event: TraceEvent, ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace_id": event.trace_id,
        "event_id": event.event_id,
        "seq": event.seq,
        "type": event.type,
        "actor": event.actor,
        "status": event.status or "unknown",
        "error_type": event.error_type or "unknown",
        "tool_name": event.tool_name or "unknown",
        "transport": event.transport or "unknown",
        "observation_source": event.observation_source or "unknown",
        "request_id": event.request_id or "unknown",
        "correlation_scope": dict(ref.get("correlation_scope", {})),
    }


def _safe_capability(version: Any, policy: RedactionPolicy) -> dict[str, Any]:
    metadata = version.metadata if isinstance(version.metadata, dict) else {}
    allowed = {
        key: metadata[key]
        for key in ("name", "description", "inputSchema", "protocol", "transport", "server_info")
        if key in metadata
    }
    safe = policy.redact_metadata(allowed)
    encoded = _canonical(safe)
    if len(encoded) > 4096:
        safe = {"summary": f"<redacted len={len(encoded)}>"}
    return {
        "version_id": version.version_id,
        "capability_id": version.capability_id,
        "content_hash": version.content_hash,
        "schema_hash": version.schema_hash,
        "source": version.source,
        "provider_version": version.provider_version,
        "environment_fingerprint": version.environment_fingerprint or "unknown",
        "metadata": safe,
    }


def validate_evidence_refs(workspace_or_bundle: Any, bundle: EvidenceBundle | None = None) -> bool:
    """Validate references locally, or against a workspace when one is supplied."""

    if bundle is None:
        bundle = workspace_or_bundle
        workspace_or_bundle = None
    if not isinstance(bundle, EvidenceBundle):
        raise EvidenceValidationError("bundle must be an EvidenceBundle")
    for ref in bundle.evidence_refs:
        if not ref.get("trace_id") or not ref.get("event_id"):
            raise EvidenceValidationError("evidence reference is incomplete")
    if workspace_or_bundle is None:
        return True
    db = workspace_or_bundle if isinstance(workspace_or_bundle, M1Database) else M1Database(workspace_or_bundle)
    traces = TraceStore(db)
    capabilities = CapabilityRegistry(db)
    for ref in bundle.evidence_refs:
        event = next(
            (item for item in traces.list_events(ref["trace_id"]) if item.event_id == ref["event_id"]),
            None,
        )
        if event is None:
            raise EvidenceValidationError(f"event reference does not exist: {ref['event_id']}")
        version_id = str(ref.get("capability_version_id") or "")
        if version_id:
            version = capabilities.get_version(version_id)
            if version is None or version.capability_id != event.capability_id:
                raise EvidenceValidationError(f"capability version reference is invalid: {version_id}")
    return True


class EvidenceBundleBuilder:
    def __init__(
        self,
        workspace_or_db: str | M1Database,
        *,
        max_occurrences: int = 24,
        max_events: int = 64,
        max_bytes: int = 48 * 1024,
        policy: RedactionPolicy | None = None,
    ) -> None:
        if max_occurrences < 1 or max_events < 1 or max_bytes < 1024:
            raise ValueError("evidence limits are too small")
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.diagnostics = DiagnosisService(self.db)
        self.traces = TraceStore(self.db)
        self.capabilities = CapabilityRegistry(self.db)
        self.max_occurrences = max_occurrences
        self.max_events = max_events
        self.max_bytes = max_bytes
        self.policy = policy or RedactionPolicy()

    def build(self, issue_id: str, *, user_constraints: list[str] | tuple[str, ...] = ()) -> EvidenceBundle:
        details = self.diagnostics.show_issue(issue_id)
        if details is None:
            raise EvidenceValidationError("issue does not exist")
        issue = details.issue.to_dict()
        occurrence_dicts: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        capabilities: dict[str, dict[str, Any]] = {}
        unknown = set()
        events_seen = 0
        for occurrence in details.occurrences[: self.max_occurrences]:
            occurrence_dict = occurrence.to_dict()
            safe_refs = [ref.to_dict() for ref in occurrence.evidence_refs]
            occurrence_dict["evidence_refs"] = safe_refs
            occurrence_dicts.append(occurrence_dict)
            for ref in safe_refs:
                validate_evidence_refs(self.db, EvidenceBundle(issue_id, issue["row_version"], "", issue, evidence_refs=(ref,)))
                refs.append(ref)
                event = next(
                    item for item in self.traces.list_events(ref["trace_id"]) if item.event_id == ref["event_id"]
                )
                if events_seen < self.max_events:
                    facts.append({"kind": "observed_event", "evidence_refs": [ref], "event": _event_summary(event, ref)})
                    events_seen += 1
                else:
                    unknown.add("evidence_event_budget")
                if event.capability_version_id:
                    version = self.capabilities.get_version(event.capability_version_id)
                    if version is not None:
                        capabilities[version.version_id] = _safe_capability(version, self.policy)
                elif event.capability_id:
                    unknown.add("capability_version_id")

        primary_capability = str(issue.get("primary_capability_id") or "")
        if primary_capability:
            latest = self.capabilities.resolve(primary_capability)
            if latest is not None and latest.version_id not in capabilities:
                capabilities[latest.version_id] = _safe_capability(latest, self.policy)

        constraints = tuple(
            str(self.policy.redact(item))[:512]
            for item in list(user_constraints)[:16]
            if str(item).strip()
        )
        truncated = len(details.occurrences) > self.max_occurrences or events_seen >= self.max_events
        base = {
            "issue_id": issue_id,
            "issue_row_version": issue["row_version"],
            "issue": issue,
            "occurrences": occurrence_dicts,
            "evidence_refs": refs,
            "observed_facts": facts,
            "capability_versions": list(capabilities.values()),
            "unknown_fields": sorted(unknown),
            "user_constraints": list(constraints),
            "truncated": truncated,
        }
        while len(_canonical(base).encode("utf-8")) > self.max_bytes and base["occurrences"]:
            base["occurrences"].pop()
            base["truncated"] = True
            allowed_events = {ref["event_id"] for occurrence in base["occurrences"] for ref in occurrence["evidence_refs"]}
            base["evidence_refs"] = [ref for ref in base["evidence_refs"] if ref["event_id"] in allowed_events]
            base["observed_facts"] = [fact for fact in base["observed_facts"] if any(ref["event_id"] in allowed_events for ref in fact["evidence_refs"])]
        if len(_canonical(base).encode("utf-8")) > self.max_bytes:
            raise EvidenceValidationError("evidence bundle exceeds configured limit")
        bundle_hash = _digest(base)
        bundle = EvidenceBundle(
            issue_id=issue_id,
            issue_row_version=int(issue["row_version"]),
            bundle_hash=bundle_hash,
            issue=base["issue"],
            occurrences=tuple(base["occurrences"]),
            evidence_refs=tuple(base["evidence_refs"]),
            observed_facts=tuple(base["observed_facts"]),
            capability_versions=tuple(base["capability_versions"]),
            unknown_fields=tuple(base["unknown_fields"]),
            user_constraints=tuple(base["user_constraints"]),
            truncated=bool(base["truncated"]),
        )
        validate_evidence_refs(self.db, bundle)
        return bundle


__all__ = ["EvidenceBundleBuilder", "EvidenceValidationError", "validate_evidence_refs"]
