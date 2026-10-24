"""Replaceable diagnosis providers; FakeProvider is the only default path."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .models import EvidenceBundle


@dataclass(frozen=True)
class ProviderRequest:
    issue_id: str
    evidence_bundle: EvidenceBundle
    model_id: str
    config: dict[str, Any]
    timeout_s: float = 15.0


@dataclass(frozen=True)
class ProviderResponse:
    content: dict[str, Any]
    provider_id: str
    model_id: str
    usage: dict[str, Any]
    latency_ms: int | None = None


class DiagnosisProvider(Protocol):
    provider_id: str
    model_id: str

    def diagnose(self, request: ProviderRequest) -> ProviderResponse:
        ...


LLMClient = DiagnosisProvider


class ProviderUnavailable(RuntimeError):
    pass


class FakeProvider:
    provider_id = "fake"
    model_id = "deterministic-m4"

    def diagnose(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        bundle = request.evidence_bundle
        issue = bundle.issue
        category = str(issue.get("category") or "unknown")
        facts = list(bundle.observed_facts[:16])
        refs = [ref for fact in facts for ref in fact.get("evidence_refs", []) if isinstance(ref, dict)]
        enough = bool(facts and refs and not bundle.truncated is True and category != "unsupported_or_unknown")
        missing = list(bundle.unknown_fields)
        if not facts:
            missing.append("observed_event")
        owner, target_type = {
            "auth_failure_observed": ("auth", "environment_action"),
            "network_failure_observed": ("network", "environment_action"),
            "upstream_boundary_failure_observed": ("unknown", "mcp_implementation_advisory"),
            "observer_failure_observed": ("observer", "agent_policy_advisory"),
            "schema_mismatch_observed": ("agent_decision", "mcp_schema"),
            "tool_failure_observed": ("mcp_server", "mcp_implementation_advisory"),
            "incomplete_observation": ("observer", "agent_policy_advisory"),
        }.get(category, ("unknown", "agent_policy_advisory"))
        hypothesis = {
            "hypothesis_id": "h1",
            "owner_kind": owner,
            "statement": f"Evidence is consistent with {category}; it is not a verified root cause.",
            "confidence_grade": "moderate_evidence" if enough else "unknown",
            "evidence_refs": refs[:8],
        }
        actions: list[dict[str, Any]] = []
        if enough:
            capability_id = str(issue.get("primary_capability_id") or "")
            actions.append(
                {
                    "target_type": target_type,
                    "target_capability_id": capability_id,
                    "risk": "medium" if target_type != "environment_action" else "high",
                    "reason": "Use the referenced evidence to validate the suspected boundary before changing behavior.",
                    "expected_effect": "Make the suspected failure boundary observable and testable.",
                    "verification_plan": "Run the paired fixture suite and inspect new trace evidence before acceptance.",
                    "patch": {"mode": "advisory_only", "category": category},
                }
            )
        content = {
            "observed_facts": facts,
            "hypotheses": [hypothesis],
            "supporting_evidence": [{"evidence_refs": refs[:8], "reason": "direct stored event reference"}] if refs else [],
            "contradicting_evidence": [],
            "missing_evidence": sorted(set(missing)),
            "confidence_grade": "moderate_evidence" if enough else "unknown",
            "recommended_actions": actions,
            "status": "review_required" if actions else "needs_evidence",
        }
        return ProviderResponse(
            content=content,
            provider_id=self.provider_id,
            model_id=request.model_id or self.model_id,
            usage={
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
                "cost": None,
                "currency": None,
            },
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )


class HTTPDiagnosisProvider:
    """Explicit opt-in JSON provider; credentials are process-only and never persisted."""

    provider_id = "http-json"

    def __init__(self, endpoint: str, *, model_id: str, api_key: str = "", timeout_s: float = 15.0) -> None:
        if not endpoint:
            raise ProviderUnavailable("diagnosis endpoint is not configured")
        self.endpoint = endpoint
        self.model_id = model_id or "unknown"
        self.api_key = api_key
        self.timeout_s = min(max(float(timeout_s), 0.1), 60.0)

    def diagnose(self, request: ProviderRequest) -> ProviderResponse:
        payload = json.dumps(
            {"issue_id": request.issue_id, "evidence_bundle": request.evidence_bundle.to_dict()},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.perf_counter()
        try:
            request_obj = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(request_obj, timeout=min(request.timeout_s, self.timeout_s)) as response:
                body = response.read(256 * 1024)
        except Exception as exc:  # pragma: no cover - endpoint availability is environment-specific
            raise ProviderUnavailable(type(exc).__name__) from exc
        try:
            content = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable("provider returned invalid JSON") from exc
        if not isinstance(content, dict):
            raise ProviderUnavailable("provider response must be a JSON object")
        usage = content.pop("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        usage = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost": usage.get("cost"),
            "currency": usage.get("currency"),
        }
        return ProviderResponse(
            content=content,
            provider_id=self.provider_id,
            model_id=self.model_id,
            usage=usage,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )


def provider_from_name(name: str, *, model_id: str = "", config: dict[str, Any] | None = None) -> DiagnosisProvider:
    normalized = (name or "fake").casefold()
    if normalized == "fake":
        return FakeProvider()
    if normalized in {"http", "http-json"}:
        values = config or {}
        endpoint = str(values.get("endpoint") or os.environ.get("SHUTU_DIAGNOSIS_ENDPOINT", ""))
        api_key = str(values.get("api_key") or os.environ.get("SHUTU_DIAGNOSIS_API_KEY", ""))
        return HTTPDiagnosisProvider(endpoint, model_id=model_id or str(values.get("model_id") or "unknown"), api_key=api_key)
    raise ProviderUnavailable(f"unsupported diagnosis provider: {name}")


__all__ = [
    "DiagnosisProvider",
    "FakeProvider",
    "HTTPDiagnosisProvider",
    "LLMClient",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderUnavailable",
    "provider_from_name",
]
