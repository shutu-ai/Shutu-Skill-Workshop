"""Evidence-gated, bounded diagnosis orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from ..models import new_id, utc_now
from ..redaction import RedactionPolicy
from ..storage import M1Database
from .evidence import EvidenceBundleBuilder
from .models import DiagnosisHypothesis, DiagnosisReport, RecommendedAction
from .providers import ProviderRequest, ProviderUnavailable, provider_from_name


_STATUSES = frozenset({"draft", "needs_evidence", "review_required", "rejected", "superseded"})
_TARGET_TYPES = frozenset(
    {
        "skill_instruction",
        "skill_script",
        "mcp_description",
        "mcp_schema",
        "mcp_implementation_advisory",
        "agent_policy_advisory",
        "environment_action",
    }
)
_RISKS = frozenset({"low", "medium", "high", "unknown"})
_INJECTION = re.compile(r"(?i)(ignore\s+(all\s+)?previous|system\s+message|developer\s+message|<\s*/?system\b)")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        if any(str(key).casefold() in {"verified_cause", "verified_fix", "api_key", "authorization", "cookie"} for key in value):
            return True
        return any(_forbidden(item) for item in value.values())
    if isinstance(value, list):
        return any(_forbidden(item) for item in value)
    return isinstance(value, str) and bool(_INJECTION.search(value))


def _refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("evidence references must be a list")
    result = []
    for item in value:
        if not isinstance(item, dict) or not item.get("trace_id") or not item.get("event_id"):
            raise ValueError("invalid evidence reference")
        result.append(dict(item))
    return result


class DiagnosisOutputError(ValueError):
    pass


class EvolutionDiagnosisService:
    def __init__(self, workspace_or_db: str | M1Database, *, builder: EvidenceBundleBuilder | None = None) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.builder = builder or EvidenceBundleBuilder(self.db)

    def diagnose(
        self,
        issue_id: str,
        *,
        provider: str = "fake",
        model_id: str = "",
        config: dict[str, Any] | None = None,
        user_constraints: list[str] | tuple[str, ...] = (),
        force: bool = False,
        max_output_bytes: int = 64 * 1024,
        max_retries: int = 1,
    ) -> DiagnosisReport:
        bundle = self.builder.build(issue_id, user_constraints=user_constraints)
        safe_config = {
            str(key): value
            for key, value in (config or {}).items()
            if str(key).casefold() not in {"api_key", "authorization", "cookie", "token", "secret"}
        }
        provider_impl = provider_from_name(provider, model_id=model_id, config=config)
        actual_model = model_id or getattr(provider_impl, "model_id", "unknown")
        config_hash = _digest(safe_config)
        base_key = _digest(
            {
                "issue_id": issue_id,
                "issue_row_version": bundle.issue_row_version,
                "evidence_bundle_hash": bundle.bundle_hash,
                "provider_id": getattr(provider_impl, "provider_id", provider),
                "model_id": actual_model,
                "config_hash": config_hash,
            }
        )
        idempotency_key = base_key if not force else _digest({"base": base_key, "rerun": new_id("diagnosis-rerun")})
        if not force:
            existing = self._get_by_idempotency(idempotency_key)
            if existing is not None:
                return existing

        request = ProviderRequest(
            issue_id=issue_id,
            evidence_bundle=bundle,
            model_id=actual_model,
            config=safe_config,
            timeout_s=min(max(float((config or {}).get("timeout_s", 15.0)), 0.1), 60.0),
        )
        response = None
        last_error: Exception | None = None
        for _ in range(max(0, min(max_retries, 3)) + 1):
            try:
                response = provider_impl.diagnose(request)
                break
            except ProviderUnavailable as exc:
                last_error = exc
        if response is None:
            raise ProviderUnavailable(str(last_error or "provider unavailable"))
        if not isinstance(response.content, dict):
            raise DiagnosisOutputError("provider output must be an object")
        if len(_canonical(response.content).encode("utf-8")) > max_output_bytes:
            raise DiagnosisOutputError("provider output exceeds configured limit")
        content = self._validate_output(RedactionPolicy().redact(response.content), bundle)
        diagnosis_id = new_id("diagnosis")
        now = utc_now()
        usage = self._usage(response.usage, response.latency_ms)
        report = DiagnosisReport(
            diagnosis_id=diagnosis_id,
            issue_id=issue_id,
            issue_row_version=bundle.issue_row_version,
            evidence_bundle_hash=bundle.bundle_hash,
            provider_id=response.provider_id,
            model_id=response.model_id,
            prompt_schema_version="m4-diagnosis-v1",
            config_hash=config_hash,
            observed_facts=tuple(content["observed_facts"]),
            hypotheses=tuple(
                DiagnosisHypothesis(
                    hypothesis_id=str(item.get("hypothesis_id") or f"h{index}"),
                    owner_kind=str(item["owner_kind"]),
                    statement=str(item["statement"]),
                    confidence_grade=str(item["confidence_grade"]),
                    evidence_refs=tuple(item["evidence_refs"]),
                )
                for index, item in enumerate(content["hypotheses"], 1)
            ),
            supporting_evidence=tuple(content["supporting_evidence"]),
            contradicting_evidence=tuple(content["contradicting_evidence"]),
            missing_evidence=tuple(content["missing_evidence"]),
            confidence_grade=str(content["confidence_grade"]),
            recommended_actions=tuple(
                RecommendedAction(
                    target_type=str(item["target_type"]),
                    target_capability_id=str(item.get("target_capability_id") or ""),
                    risk=str(item["risk"]),
                    reason=str(item["reason"]),
                    expected_effect=str(item["expected_effect"]),
                    verification_plan=str(item["verification_plan"]),
                    patch=dict(item.get("patch") or {}),
                )
                for item in content["recommended_actions"]
            ),
            status=str(content["status"]),
            usage=usage,
            created_at=now,
        )
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO diagnosis_reports(
                        diagnosis_id, idempotency_key, issue_id, issue_row_version,
                        evidence_bundle_hash, provider_id, model_id, prompt_schema_version,
                        config_hash, status, confidence_grade, report_json,
                        evidence_bundle_json, usage_json, created_at, updated_at, row_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        diagnosis_id,
                        idempotency_key,
                        issue_id,
                        bundle.issue_row_version,
                        bundle.bundle_hash,
                        report.provider_id,
                        report.model_id,
                        report.prompt_schema_version,
                        config_hash,
                        report.status,
                        report.confidence_grade,
                        _canonical(report.to_dict()),
                        _canonical(bundle.to_dict()),
                        _canonical(usage),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            cached = self._get_by_idempotency(idempotency_key)
            if cached is not None:
                return cached
            raise
        return report

    def get(self, diagnosis_id: str) -> DiagnosisReport | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT report_json FROM diagnosis_reports WHERE diagnosis_id = ?", (diagnosis_id,)).fetchone()
        return self._report_from_json(row[0]) if row else None

    get_report = get

    def list(self, issue_id: str | None = None) -> list[DiagnosisReport]:
        query = "SELECT report_json FROM diagnosis_reports"
        values: list[Any] = []
        if issue_id:
            query += " WHERE issue_id = ?"
            values.append(issue_id)
        query += " ORDER BY created_at, diagnosis_id"
        with self.db.connection() as connection:
            rows = connection.execute(query, values).fetchall()
        return [self._report_from_json(row[0]) for row in rows]

    def _get_by_idempotency(self, key: str) -> DiagnosisReport | None:
        with self.db.connection() as connection:
            row = connection.execute("SELECT report_json FROM diagnosis_reports WHERE idempotency_key = ?", (key,)).fetchone()
        return self._report_from_json(row[0]) if row else None

    @staticmethod
    def _usage(value: Any, latency_ms: int | None) -> dict[str, Any]:
        value = value if isinstance(value, dict) else {}
        return {
            "input_tokens": value.get("input_tokens"),
            "output_tokens": value.get("output_tokens"),
            "total_tokens": value.get("total_tokens"),
            "cost": value.get("cost"),
            "currency": value.get("currency"),
            "latency_ms": latency_ms,
        }

    @staticmethod
    def _validate_output(value: dict[str, Any], bundle: Any) -> dict[str, Any]:
        if _forbidden(value):
            raise DiagnosisOutputError("provider output contains forbidden claim, credential, or prompt injection")
        observed = value.get("observed_facts", [])
        hypotheses = value.get("hypotheses", [])
        supporting = value.get("supporting_evidence", [])
        contradicting = value.get("contradicting_evidence", [])
        actions = value.get("recommended_actions", [])
        if not all(isinstance(item, list) for item in (observed, hypotheses, supporting, contradicting, actions)):
            raise DiagnosisOutputError("provider output collections must be lists")
        if len(observed) > 64 or len(hypotheses) > 16 or len(actions) > 16:
            raise DiagnosisOutputError("provider output is too large")
        allowed_refs = {
            _canonical(ref)
            for ref in bundle.evidence_refs
        }
        for fact in observed:
            if not isinstance(fact, dict) or not _refs(fact.get("evidence_refs", [])):
                raise DiagnosisOutputError("observed fact is not evidence-bound")
            if any(_canonical(ref) not in allowed_refs for ref in fact["evidence_refs"]):
                raise DiagnosisOutputError("observed fact cites an unknown evidence reference")
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict) or not str(hypothesis.get("owner_kind")) or not str(hypothesis.get("statement")):
                raise DiagnosisOutputError("invalid hypothesis")
            hypothesis["evidence_refs"] = _refs(hypothesis.get("evidence_refs", []))
            if any(_canonical(ref) not in allowed_refs for ref in hypothesis["evidence_refs"]):
                raise DiagnosisOutputError("hypothesis cites an unknown evidence reference")
        for evidence_group in (supporting, contradicting):
            for item in evidence_group:
                if not isinstance(item, dict):
                    raise DiagnosisOutputError("invalid evidence group")
                item["evidence_refs"] = _refs(item.get("evidence_refs", []))
                if any(_canonical(ref) not in allowed_refs for ref in item["evidence_refs"]):
                    raise DiagnosisOutputError("evidence group cites an unknown reference")
        allowed_capabilities = {str(bundle.issue.get("primary_capability_id") or "")}
        allowed_capabilities.update(str(item.get("capability_id") or "") for item in bundle.capability_versions)
        for action in actions:
            if not isinstance(action, dict) or str(action.get("target_type")) not in _TARGET_TYPES:
                raise DiagnosisOutputError("invalid proposal target type")
            if str(action.get("risk") or "unknown") not in _RISKS:
                raise DiagnosisOutputError("invalid proposal risk")
            for key in ("reason", "expected_effect", "verification_plan"):
                if not str(action.get(key) or "").strip() or len(str(action[key])) > 2048:
                    raise DiagnosisOutputError("proposal action is incomplete or too long")
            target = str(action.get("target_capability_id") or "")
            if target and target not in allowed_capabilities:
                raise DiagnosisOutputError("proposal target is outside the evidence bundle")
            patch = action.get("patch") or {}
            if not isinstance(patch, dict) or any(str(key).casefold() in {"apply", "deploy", "production_write"} for key in patch):
                raise DiagnosisOutputError("provider proposed an execution side effect")
        status = str(value.get("status") or "draft")
        if status not in _STATUSES:
            raise DiagnosisOutputError("invalid diagnosis status")
        if not actions:
            status = "needs_evidence"
        value["observed_facts"] = observed
        value["hypotheses"] = hypotheses
        value["supporting_evidence"] = supporting
        value["contradicting_evidence"] = contradicting
        value["missing_evidence"] = [str(item)[:256] for item in value.get("missing_evidence", [])[:64]]
        value["recommended_actions"] = actions
        value["confidence_grade"] = str(value.get("confidence_grade") or "unknown")
        value["status"] = status
        return value

    @staticmethod
    def _report_from_json(raw: str) -> DiagnosisReport:
        value = json.loads(raw)
        return DiagnosisReport(
            diagnosis_id=value["diagnosis_id"],
            issue_id=value["issue_id"],
            issue_row_version=int(value["issue_row_version"]),
            evidence_bundle_hash=value["evidence_bundle_hash"],
            provider_id=value["provider_id"],
            model_id=value["model_id"],
            prompt_schema_version=value["prompt_schema_version"],
            config_hash=value["config_hash"],
            observed_facts=tuple(value.get("observed_facts", [])),
            hypotheses=tuple(
                DiagnosisHypothesis(
                    hypothesis_id=item["hypothesis_id"],
                    owner_kind=item["owner_kind"],
                    statement=item["statement"],
                    confidence_grade=item["confidence_grade"],
                    evidence_refs=tuple(item.get("evidence_refs", [])),
                )
                for item in value.get("hypotheses", [])
            ),
            supporting_evidence=tuple(value.get("supporting_evidence", [])),
            contradicting_evidence=tuple(value.get("contradicting_evidence", [])),
            missing_evidence=tuple(value.get("missing_evidence", [])),
            confidence_grade=value["confidence_grade"],
            recommended_actions=tuple(
                RecommendedAction(
                    target_type=item["target_type"],
                    target_capability_id=item.get("target_capability_id", ""),
                    risk=item["risk"],
                    reason=item["reason"],
                    expected_effect=item["expected_effect"],
                    verification_plan=item["verification_plan"],
                    patch=dict(item.get("patch") or {}),
                )
                for item in value.get("recommended_actions", [])
            ),
            status=value["status"],
            usage=dict(value.get("usage") or {}),
            created_at=value["created_at"],
            row_version=int(value.get("row_version", 0)),
        )


DiagnosisAdvisor = EvolutionDiagnosisService

__all__ = [
    "DiagnosisAdvisor",
    "DiagnosisOutputError",
    "EvolutionDiagnosisService",
]
