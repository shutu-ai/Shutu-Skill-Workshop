"""Unified, evidence-first Skill and MCP quality evaluation.

This module is deliberately small.  It reuses CapabilityRegistry, TraceStore,
and the existing evaluation contracts instead of creating a second diagnosis
ledger or storing source files in trace payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .capabilities import CapabilityRegistry
from .models import Capability, new_id, utc_now
from .redaction import RedactionPolicy
from .storage import M1Database


QUALITY_VERDICTS = frozenset({"IMPROVED", "EQUIVALENT", "REGRESSED", "INCONCLUSIVE", "BLOCKED_ENV"})
EVIDENCE_TIERS = frozenset(
    {
        "HOST_OBSERVED",
        "ARTIFACT_ACCESS_OBSERVED",
        "EXPERIMENT_CONDITION",
        "FIXTURE_EXECUTED",
        "REAL_TASK_EXECUTED",
        "REAL_AGENT_OBSERVED",
        "REAL_MCP_TOOL_EXECUTED",
        "REAL_ARTIFACT_ACTIVATED",
        "INFERRED",
        "NOT_OBSERVABLE",
        "UNKNOWN",
    }
)
SELECTION_MODES = frozenset({"NATURAL", "FORCED", "DISABLED", "UNKNOWN"})
OWNERS = frozenset(
    {
        "agent_decision",
        "skill",
        "mcp_tool",
        "mcp_server",
        "provider",
        "host",
        "environment",
        "user",
        "unknown",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _items(value: Iterable[Any]) -> list[Any]:
    return list(value)


@dataclass(frozen=True)
class SkillObservation:
    capability_id: str
    skill_name: str
    scope: str
    source: str
    source_root: str
    version_id: str
    content_hash: str
    frontmatter: dict[str, str]
    file_count: int
    file_manifest_hash: str
    statuses: dict[str, bool | None] = field(default_factory=dict)
    evidence_tier: str = "ARTIFACT_ACCESS_OBSERVED"
    observation_completeness: str = "complete"
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors)
        return value


@dataclass(frozen=True)
class SkillSelectionLabel:
    case_id: str
    task_input_hash: str
    target_skill_id: str
    should_use: str
    expected_tools: tuple[str, ...] = ()
    scenario: str = ""
    label_source: str = "human_review"
    selection_mode: str = "NATURAL"
    observed_invocation: bool | None = None
    observation_evidence_tier: str = "NOT_OBSERVABLE"
    host_event_ref: str = ""
    source_scope: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.should_use not in {"yes", "no", "review"}:
            raise ValueError("should_use must be yes, no, or review")
        if self.selection_mode not in SELECTION_MODES:
            raise ValueError("unsupported selection mode")
        if self.observation_evidence_tier not in EVIDENCE_TIERS:
            raise ValueError("unsupported observation evidence tier")
        if not str(self.label_source).strip():
            raise ValueError("label_source is required")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["expected_tools"] = list(self.expected_tools)
        return value


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    task_input: dict[str, Any]
    category: str
    holdout: bool = False
    allowed_tools: tuple[str, ...] = ()
    label_source: str = "human_review"
    target_skill_id: str = ""

    @property
    def task_input_hash(self) -> str:
        return digest(self.task_input)

    def runner_input(self) -> dict[str, Any]:
        """Return only the input the agent is allowed to see."""

        return dict(self.task_input)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["allowed_tools"] = list(self.allowed_tools)
        value["task_input_hash"] = self.task_input_hash
        return value


@dataclass(frozen=True)
class QualityMetric:
    name: str
    value: Any
    source: str
    sample_size: int
    missing_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityEvidence:
    capability_kind: str
    capability_id: str
    capability_version_id: str | None = None
    task_case_id: str | None = None
    host: str = "other"
    model_provider: str = "unknown"
    model_id: str = "unknown"
    trace_id: str | None = None
    event_refs: tuple[str, ...] = ()
    evidence_tier: str = "UNKNOWN"
    selection_mode: str = "UNKNOWN"
    observation_completeness: str = "unknown"
    oracle_id: str | None = None
    oracle_version: str | None = None
    verdict: str = "INCONCLUSIVE"
    metrics: dict[str, Any] = field(default_factory=dict)
    environment_fingerprint: str = ""
    artifact_hash: str | None = None
    correlation_quality: str = "unknown"
    unknown_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evidence_tier not in EVIDENCE_TIERS:
            raise ValueError("unsupported evidence tier")
        if self.selection_mode not in SELECTION_MODES:
            raise ValueError("unsupported selection mode")
        if self.verdict not in QUALITY_VERDICTS:
            raise ValueError("unsupported quality verdict")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["event_refs"] = list(self.event_refs)
        value["unknown_fields"] = list(self.unknown_fields)
        return value


@dataclass(frozen=True)
class QualityFinding:
    finding_id: str
    target_kind: str
    target_id: str
    classification: str
    primary_owner: str
    contributing_owners: tuple[str, ...] = ()
    confidence: str = "unknown"
    evidence_refs: tuple[str, ...] = ()
    required_verification: tuple[str, ...] = ()
    proposal: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.primary_owner not in OWNERS:
            raise ValueError("unsupported primary owner")
        if any(owner not in OWNERS for owner in self.contributing_owners):
            raise ValueError("unsupported contributing owner")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["contributing_owners"] = list(self.contributing_owners)
        value["evidence_refs"] = list(self.evidence_refs)
        value["required_verification"] = list(self.required_verification)
        return value


@dataclass(frozen=True)
class QualityReport:
    report_id: str
    report_kind: str
    status: str
    generated_at: str
    environment_fingerprint: str
    summary: dict[str, Any]
    metrics: tuple[QualityMetric, ...] = ()
    findings: tuple[QualityFinding, ...] = ()
    evidence: tuple[QualityEvidence, ...] = ()
    results: tuple[dict[str, Any], ...] = ()
    missing_fields: tuple[str, ...] = ()
    source: str = "shutu_workshop.quality"

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_kind": self.report_kind,
            "status": self.status,
            "generated_at": self.generated_at,
            "environment_fingerprint": self.environment_fingerprint,
            "summary": dict(self.summary),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "findings": [finding.to_dict() for finding in self.findings],
            "evidence": [item.to_dict() for item in self.evidence],
            "results": [dict(item) for item in self.results],
            "missing_fields": list(self.missing_fields),
            "source": self.source,
        }


class SkillRegistry:
    """Discover explicitly authorised local Skill directories.

    The registry stores only metadata and file digests.  It never follows a
    symlink while building a snapshot and never stores source file contents.
    """

    def __init__(self, workspace_or_db: str | Path | M1Database, *, policy: RedactionPolicy | None = None) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.capabilities = CapabilityRegistry(self.db)
        self.policy = policy or RedactionPolicy()

    def discover(
        self,
        roots: Sequence[str | Path],
        *,
        scope: str = "project",
        source: str = "explicit",
        max_files: int = 2048,
    ) -> list[SkillObservation]:
        if scope not in {"global", "project", "plugin"}:
            raise ValueError("scope must be global, project, or plugin")
        if not roots:
            return []
        observations: list[SkillObservation] = []
        seen: set[Path] = set()
        for value in roots:
            requested = Path(value).expanduser()
            if requested.is_file() and requested.name.casefold() == "skill.md":
                candidates = [requested.parent]
            elif requested.is_dir():
                candidates = [requested] if (requested / "SKILL.md").is_file() else [item.parent for item in requested.rglob("SKILL.md") if not item.is_symlink()]
            else:
                continue
            for candidate in candidates:
                try:
                    root = candidate.resolve()
                except OSError:
                    continue
                if root in seen or not root.is_dir() or (root / "SKILL.md").is_symlink():
                    continue
                seen.add(root)
                observations.append(self._snapshot(root, scope=scope, source=source, max_files=max_files))
        return observations

    scan = discover

    def _snapshot(self, root: Path, *, scope: str, source: str, max_files: int) -> SkillObservation:
        errors: list[str] = []
        frontmatter: dict[str, str] = {}
        skill_file = root / "SKILL.md"
        try:
            raw = skill_file.read_bytes()
            frontmatter = self._frontmatter(raw.decode("utf-8"))
        except UnicodeDecodeError:
            errors.append("skill_md_not_utf8")
        except OSError as exc:
            errors.append(f"skill_md_{type(exc).__name__}")
        name = frontmatter.get("name", "").strip() or root.name
        root_digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
        capability_id = f"skill:{scope}:{name}:{root_digest[:12]}"
        entries: list[dict[str, Any]] = []
        try:
            walker = sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold())
        except OSError:
            walker = []
            errors.append("directory_walk_failed")
        for item in walker:
            if len(entries) >= max_files:
                errors.append("file_limit_exceeded")
                break
            try:
                if item.is_symlink() or not item.is_file() or not item.resolve().is_relative_to(root):
                    continue
                raw = item.read_bytes()
                entries.append(
                    {
                        "path": item.relative_to(root).as_posix(),
                        "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            except (OSError, ValueError):
                errors.append(f"unreadable:{item.name}")
        tree_hash = digest(entries)
        version_id = f"skill-version-{hashlib.sha256((capability_id + tree_hash).encode()).hexdigest()[:24]}"
        metadata = {"name": name, "scope": scope, "source": source, "root_digest": root_digest}
        self.capabilities.register(
            self.capabilities.get(capability_id)
            or Capability(
                capability_id=capability_id,
                kind="skill",
                adapter_id="skill-registry",
                locator=f"{scope}/{name}",
                metadata=metadata,
                discovered_from=source,
            )
        )
        version = self.capabilities.get_version(version_id)
        if version is None:
            version = self.capabilities.snapshot(
                capability_id,
                source="skill_scan",
                version_id=version_id,
                content_hash=tree_hash,
                snapshot_ref=f"skill-tree:{root_digest}",
                environment_fingerprint=digest({"scope": scope, "source": source})[:24],
                metadata={
                    "frontmatter": self.policy.redact_metadata(frontmatter),
                    "files": entries,
                    "file_manifest_hash": tree_hash,
                },
            )
        status = {"discovered": True, "available": not errors, "eligible": not errors, "loaded": None, "invoked": None, "followed": None}
        return SkillObservation(
            capability_id=capability_id,
            skill_name=name,
            scope=scope,
            source=source,
            source_root=str(root),
            version_id=version.version_id,
            content_hash=tree_hash,
            frontmatter={key: str(value) for key, value in frontmatter.items() if key in {"name", "description"}},
            file_count=len(entries),
            file_manifest_hash=tree_hash,
            statuses=status,
            evidence_tier="ARTIFACT_ACCESS_OBSERVED",
            observation_completeness="complete" if not errors else "partial",
            errors=tuple(sorted(set(errors))),
        )

    @staticmethod
    def _frontmatter(text: str) -> dict[str, str]:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        result: dict[str, str] = {}
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            value = value.strip().strip("'\"")
            if key.strip() in {"name", "description"}:
                result[key.strip()] = value
        return result


class SkillRoutingEvaluator:
    """Evaluate labelled routing without inventing host invocation truth."""

    @staticmethod
    def _trusted_host_event(
        label: SkillSelectionLabel,
        host_events: Mapping[str, Mapping[str, Any]] | None,
    ) -> tuple[bool, str]:
        """Return true only for a scoped, native lifecycle event we can verify."""

        if label.selection_mode != "NATURAL":
            return False, "selection_mode_not_natural"
        if label.observed_invocation is None:
            return False, "invocation_value_missing"
        if label.observation_evidence_tier != "HOST_OBSERVED":
            return False, "host_evidence_tier_mismatch"
        if not label.host_event_ref:
            return False, "host_event_ref_missing"
        if not host_events:
            return False, "host_event_index_missing"
        event = host_events.get(label.host_event_ref)
        if not isinstance(event, Mapping):
            return False, "host_event_ref_not_found"
        event_type = str(event.get("type") or event.get("event_type") or "")
        if event_type not in {"skill.loaded", "skill.invoked"}:
            return False, "host_event_not_skill_lifecycle"
        event_ref = str(event.get("event_id") or event.get("host_event_ref") or "")
        if not event_ref:
            return False, "host_event_id_missing"
        if event_ref != label.host_event_ref:
            return False, "host_event_ref_mismatch"
        event_scope = event.get("source_scope") or event.get("correlation_scope") or {}
        if not isinstance(event_scope, Mapping):
            return False, "host_event_scope_missing"
        expected_id = label.target_skill_id
        actual_id = str(event.get("target_skill_id") or event.get("capability_id") or event_scope.get("capability_id") or "")
        if expected_id and actual_id and actual_id != expected_id:
            return False, "skill_scope_mismatch"
        if expected_id and not actual_id:
            return False, "skill_scope_missing"
        for key in ("scope", "version_id", "capability_version_id"):
            expected = str(label.source_scope.get(key) or "")
            actual = str(event.get(key) or event_scope.get(key) or "")
            if expected and actual and expected != actual:
                return False, f"{key}_mismatch"
            if expected and not actual:
                return False, f"{key}_missing"
        return True, ""

    def evaluate(
        self,
        labels: Sequence[SkillSelectionLabel],
        *,
        environment_fingerprint: str = "",
        host_events: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> QualityReport:
        if len({label.case_id for label in labels}) != len(labels):
            raise ValueError("routing case_id must be unique")
        natural = [label for label in labels if label.selection_mode == "NATURAL"]
        forced = [label for label in labels if label.selection_mode == "FORCED"]
        trusted: dict[str, bool] = {}
        unknown_reasons: dict[str, str] = {}
        for label in natural:
            ok, reason = self._trusted_host_event(label, host_events)
            if ok and label.should_use in {"yes", "no"}:
                trusted[label.case_id] = bool(label.observed_invocation)
            else:
                unknown_reasons[label.case_id] = reason or "label_not_eligible"
        usable = [label for label in natural if label.case_id in trusted]
        unknown = [label.case_id for label in natural if label.case_id not in trusted]
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for label in usable:
            observed = trusted[label.case_id]
            if label.should_use == "yes" and observed:
                counts["tp"] += 1
            elif label.should_use == "no" and observed:
                counts["fp"] += 1
            elif label.should_use == "yes":
                counts["fn"] += 1
            else:
                counts["tn"] += 1
        metrics: list[QualityMetric] = []
        complete_natural = bool(natural) and len(usable) == len(natural)
        if complete_natural:
            precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
            recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            for name, value in (("precision", precision), ("recall", recall), ("f1", f1)):
                metrics.append(QualityMetric(name, value, "host_observed_native_skill_lifecycle", len(natural)))
            status = "PASS"
        else:
            reason = "host_skill_invocation_not_exposed" if not host_events else "native_skill_lifecycle_evidence_incomplete"
            for name in ("precision", "recall", "f1"):
                metrics.append(QualityMetric(name, "N/A", "not_observable", len(usable), reason))
            status = "PARTIAL" if labels else "NO_DATA"
        metrics.extend(
            (
                QualityMetric("tp", counts["tp"], "routing_labels", len(usable), "" if usable else "no_observed_truth"),
                QualityMetric("fp", counts["fp"], "routing_labels", len(usable), "" if usable else "no_observed_truth"),
                QualityMetric("fn", counts["fn"], "routing_labels", len(usable), "" if usable else "no_observed_truth"),
                QualityMetric("tn", counts["tn"], "routing_labels", len(usable), "" if usable else "no_observed_truth"),
            )
        )
        results = tuple(label.to_dict() for label in labels)
        return QualityReport(
            report_id=new_id("quality-routing"),
            report_kind="skill_routing",
            status=status,
            generated_at=utc_now(),
            environment_fingerprint=environment_fingerprint,
            summary={
                "natural_sample_size": len(natural),
                "forced_excluded": len(forced),
                "unknown_case_ids": unknown,
                "unknown_reasons": unknown_reasons,
                "natural_observed_sample_size": len(usable),
                "natural_denominator_completeness": "COMPLETE" if complete_natural else "INCOMPLETE",
                "selection_bias_risk": "NONE" if complete_natural else "UNKNOWN_DENOMINATOR",
                "forced_condition": "FORCED_SKILL_CONDITION" if forced else "",
                "selection_truth": "HOST_OBSERVED" if complete_natural else "NOT_OBSERVABLE",
            },
            metrics=tuple(metrics),
            results=results,
            missing_fields=("skill_actual_invocation_observed", "host_event_ref", "source_scope") if unknown else (),
        )


@dataclass(frozen=True)
class _ArmExecution:
    arm: str
    case_id: str
    execution_status: str
    task_result: str
    oracle_result: dict[str, Any]
    artifact_hash: str
    task_input_hash: str
    duration_ms: int | None
    output_digest: str
    errors: tuple[str, ...] = ()
    evidence_tier: str = "FIXTURE_EXECUTED"
    execution_verdict: str = "unknown"
    runner_id: str = "shutu.skill-three-arm-runner"
    runner_version: str = "1"
    host_version: str = "unknown"
    model_id: str = "unknown"
    environment_fingerprint: str = ""
    execution_manifest_hash: str = ""
    trace_id: str = ""
    event_refs: tuple[str, ...] = ()
    artifact_prelaunch_hash: str = ""
    artifact_runtime_hash: str = ""
    artifact_postrun_hash: str = ""
    skill_condition_verified: str = "UNKNOWN"
    skill_invocation_observed: str = "NOT_OBSERVABLE"
    condition_reason: str = ""
    effective_skill_inventory: tuple[dict[str, Any], ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors)
        return value


class ThreeArmQualityRunner:
    """Run isolated A/B/C artifacts and judge them with an external Oracle."""

    runner_id = "shutu.skill-three-arm-runner"
    runner_version = "1"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        executor: Callable[[str, QualityCase, Path, str, str], Mapping[str, Any] | _ArmExecution] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.executor = executor

    def run(
        self,
        cases: Sequence[QualityCase],
        artifacts: Mapping[str, str | Path],
        oracle: Callable[[QualityCase, Any], Mapping[str, Any] | bool] | None,
        *,
        oracle_id: str = "quality-oracle",
        oracle_version: str = "1",
        environment_fingerprint: str = "",
        persist: "QualityService | None" = None,
    ) -> QualityReport:
        required = {"no_skill", "baseline", "candidate"}
        if set(artifacts) != required:
            raise ValueError("three-arm artifacts must contain no_skill, baseline, and candidate")
        if not cases:
            raise ValueError("three-arm cases cannot be empty")
        if len({case.case_id for case in cases}) != len(cases):
            raise ValueError("three-arm case_id must be unique")
        categories = {case.category for case in cases}
        if not {"target", "normal-regression", "holdout"}.issubset(categories):
            raise ValueError("cases must include target, normal-regression, and holdout")
        frozen: dict[str, tuple[Path, str]] = {}
        for arm, value in artifacts.items():
            root = Path(value).expanduser().resolve()
            if not root.is_dir():
                raise ValueError(f"artifact root is missing: {arm}")
            frozen[arm] = (root, _tree_hash(root))
        if any(item.name.casefold() == "skill.md" for item in frozen["no_skill"][0].rglob("*") if not item.is_symlink()):
            raise ValueError("no_skill arm still contains a Skill artifact")
        if frozen["baseline"][1] == frozen["candidate"][1]:
            raise ValueError("baseline and candidate artifacts must have different frozen hashes")
        env = environment_fingerprint or digest({"runner": self.runner_id, "version": self.runner_version, "timeout": self.timeout_seconds})[:24]
        results: list[_ArmExecution] = []
        for case in cases:
            for arm in ("no_skill", "baseline", "candidate"):
                if self.executor is None:
                    results.append(self._execute(arm, case, *frozen[arm], oracle=oracle, environment_fingerprint=env))
                else:
                    results.append(self._execute_external(arm, case, *frozen[arm], oracle=oracle, environment_fingerprint=env))
        result_dicts = tuple(item.to_dict() for item in results)
        baseline = {item.case_id: item for item in results if item.arm == "baseline"}
        candidate = {item.case_id: item for item in results if item.arm == "candidate"}
        unknown = [item.case_id for item in results if item.task_result == "unknown"]
        regressions: list[str] = []
        if unknown:
            verdict = "BLOCKED_ENV" if all(item.execution_status in {"blocked_env", "not_executed"} for item in results if item.task_result == "unknown") else "INCONCLUSIVE"
        else:
            baseline_success = sum(item.task_result == "success" for item in baseline.values())
            candidate_success = sum(item.task_result == "success" for item in candidate.values())
            regressions = sorted(case_id for case_id in baseline if baseline[case_id].task_result == "success" and candidate[case_id].task_result != "success")
            if candidate_success > baseline_success and not regressions:
                verdict = "IMPROVED"
            elif candidate_success == baseline_success and not regressions:
                verdict = "EQUIVALENT"
            else:
                verdict = "REGRESSED"
        holdout_ids = {case.case_id for case in cases if case.holdout or case.category == "holdout"}
        holdout_regressions = [case_id for case_id in regressions if case_id in holdout_ids]
        metrics = (
            QualityMetric("task_success_rate.no_skill", _rate(results, "no_skill"), "independent_oracle", len(cases), "unknown_execution" if unknown else ""),
            QualityMetric("task_success_rate.baseline", _rate(results, "baseline"), "independent_oracle", len(cases), "unknown_execution" if unknown else ""),
            QualityMetric("task_success_rate.candidate", _rate(results, "candidate"), "independent_oracle", len(cases), "unknown_execution" if unknown else ""),
            QualityMetric("sample_size", len(cases), "frozen_suite", len(cases)),
            QualityMetric("tool_call_count", "N/A", "runner_not_observable", 0, "runner_does_not_expose_tool_events"),
            QualityMetric("token_count", "N/A", "runner_not_observable", 0, "provider_usage_not_exposed"),
        )
        evidence = tuple(
            QualityEvidence(
                capability_kind="skill",
                capability_id=case.target_skill_id or f"skill-arm:{arm}",
                task_case_id=case.case_id,
                host="other",
                evidence_tier=item.evidence_tier,
                selection_mode="DISABLED" if arm == "no_skill" else "FORCED",
                observation_completeness="complete" if item.execution_status == "completed" else "unknown",
                oracle_id=oracle_id,
                oracle_version=oracle_version,
                # A single arm is not a comparison verdict.
                verdict="INCONCLUSIVE",
                metrics={"task_result": item.task_result, "duration_ms": item.duration_ms, "comparison_verdict": verdict},
                environment_fingerprint=env,
                artifact_hash=item.artifact_hash,
                correlation_quality="derived",
                unknown_fields=("skill_actual_invocation_observed",),
            )
            for item in results
            for case in cases
            if item.case_id == case.case_id
            for arm in (item.arm,)
        )
        report = QualityReport(
            report_id=new_id("quality-three-arm"),
            report_kind="skill_three_arm",
            status="PASS" if verdict in {"IMPROVED", "EQUIVALENT", "REGRESSED"} else "PARTIAL",
            generated_at=utc_now(),
            environment_fingerprint=env,
            summary={
                "verdict": verdict,
                "oracle_id": oracle_id,
                "oracle_version": oracle_version,
                "arm_artifact_hashes": {arm: value[1] for arm, value in frozen.items()},
                "case_categories": {category: sum(case.category == category for case in cases) for category in sorted(categories)},
                "unknown_case_ids": sorted(set(unknown)),
            "regressions": regressions,
            "holdout_regressions": holdout_regressions,
                "skill_actual_invocation_observed": False,
                "skill_invocation_evidence": "NOT_OBSERVABLE",
            },
            metrics=metrics,
            evidence=evidence,
            results=result_dicts,
            missing_fields=("trace_id", "event_refs", "skill_actual_invocation_observed", "token_count", "cost"),
        )
        if persist:
            persist.save(report)
        return report

    def _execute_external(
        self,
        arm: str,
        case: QualityCase,
        root: Path,
        artifact_hash: str,
        *,
        oracle: Callable[[QualityCase, Any], Mapping[str, Any] | bool] | None,
        environment_fingerprint: str,
    ) -> _ArmExecution:
        """Run a real executor without giving it the Oracle or another arm's result."""

        try:
            value = self.executor(arm, case, root, artifact_hash, environment_fingerprint)  # type: ignore[misc]
        except Exception as exc:  # execution failures remain unknown evidence
            return _ArmExecution(
                arm,
                case.case_id,
                "failed",
                "unknown",
                {"verdict": "unknown", "reason": "executor_error"},
                artifact_hash,
                case.task_input_hash,
                None,
                "",
                (type(exc).__name__,),
                "REAL_AGENT_OBSERVED",
                environment_fingerprint=environment_fingerprint,
                skill_condition_verified="UNKNOWN",
            )
        if isinstance(value, _ArmExecution):
            item = value
            output_for_oracle: Any = value.evidence or value.to_dict()
        else:
            payload = dict(value)
            output_for_oracle = payload.get("oracle_input", payload.get("output", payload))
            item = _ArmExecution(
                arm=arm,
                case_id=case.case_id,
                execution_status=str(payload.get("execution_status", "failed")),
                task_result=str(payload.get("task_result", "unknown")),
                oracle_result=dict(payload.get("oracle_result") or {"verdict": "unknown"}),
                artifact_hash=artifact_hash,
                task_input_hash=case.task_input_hash,
                duration_ms=payload.get("duration_ms"),
                output_digest=str(payload.get("output_digest") or digest(output_for_oracle)),
                errors=tuple(str(error) for error in payload.get("errors", ())),
                evidence_tier=str(payload.get("evidence_tier") or "REAL_AGENT_OBSERVED"),
                execution_verdict=str(payload.get("execution_verdict") or payload.get("task_result", "unknown")),
                runner_id=str(payload.get("runner_id") or self.runner_id),
                runner_version=str(payload.get("runner_version") or self.runner_version),
                host_version=str(payload.get("host_version") or "unknown"),
                model_id=str(payload.get("model_id") or "unknown"),
                environment_fingerprint=str(payload.get("environment_fingerprint") or environment_fingerprint),
                execution_manifest_hash=str(payload.get("execution_manifest_hash") or ""),
                trace_id=str(payload.get("trace_id") or ""),
                event_refs=tuple(str(ref) for ref in payload.get("event_refs", ())),
                artifact_prelaunch_hash=str(payload.get("artifact_prelaunch_hash") or artifact_hash),
                artifact_runtime_hash=str(payload.get("artifact_runtime_hash") or ""),
                artifact_postrun_hash=str(payload.get("artifact_postrun_hash") or ""),
                skill_condition_verified=str(payload.get("skill_condition_verified") or "UNKNOWN"),
                skill_invocation_observed=str(payload.get("skill_invocation_observed") or "NOT_OBSERVABLE"),
                evidence=payload,
            )
        if item.execution_status != "completed" or oracle is None:
            return item
        try:
            oracle_value = oracle(case, output_for_oracle)
            oracle_result = dict(oracle_value) if isinstance(oracle_value, Mapping) else {"passed": bool(oracle_value)}
            task_result = "success" if oracle_result.get("passed") is True or oracle_result.get("task_result") == "success" or str(oracle_result.get("verdict", "")).casefold() in {"pass", "passed", "success", "ok"} else "failed" if oracle_result.get("passed") is False or oracle_result.get("task_result") == "failed" or str(oracle_result.get("verdict", "")).casefold() in {"fail", "failed", "error"} else "unknown"
            return _replace_arm(item, task_result=task_result, oracle_result=oracle_result, execution_verdict=task_result)
        except Exception as exc:
            return _replace_arm(
                item,
                task_result="unknown",
                oracle_result={"verdict": "unknown", "reason": "oracle_error", "error_type": type(exc).__name__},
                execution_verdict="unknown",
                errors=tuple(item.errors) + ("oracle_error",),
            )

    def _execute(
        self,
        arm: str,
        case: QualityCase,
        root: Path,
        artifact_hash: str,
        *,
        oracle: Callable[[QualityCase, Any], Mapping[str, Any] | bool] | None,
        environment_fingerprint: str,
    ) -> _ArmExecution:
        validator = root / "scripts" / "validator.py"
        started = time.perf_counter()
        if not validator.is_file():
            return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {"verdict": "unknown", "reason": "validator_missing"}, artifact_hash, case.task_input_hash, None, "", ("validator_missing",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, skill_condition_verified="UNKNOWN")
        try:
            completed = subprocess.run(
                [sys.executable, str(validator)],
                input=json.dumps(case.runner_input(), ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                cwd=str(root),
                env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
                check=False,
            )
            duration = max(0, int((time.perf_counter() - started) * 1000))
            if completed.returncode != 0:
                return _ArmExecution(arm, case.case_id, "failed", "unknown", {"verdict": "unknown", "reason": "runner_nonzero"}, artifact_hash, case.task_input_hash, duration, "", (f"runner_exit_{completed.returncode}",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, skill_condition_verified="UNKNOWN")
            try:
                output = json.loads(completed.stdout)
            except (TypeError, ValueError, json.JSONDecodeError):
                return _ArmExecution(arm, case.case_id, "failed", "unknown", {"verdict": "unknown", "reason": "result_missing_or_invalid"}, artifact_hash, case.task_input_hash, duration, "", ("result_missing_or_invalid",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, skill_condition_verified="UNKNOWN")
            runtime_hash = _tree_hash(root)
            if runtime_hash != artifact_hash:
                return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {"verdict": "unknown", "reason": "artifact_mutated_during_run"}, artifact_hash, case.task_input_hash, duration, "", ("artifact_mutated_during_run",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, artifact_prelaunch_hash=artifact_hash, artifact_runtime_hash=runtime_hash, artifact_postrun_hash=runtime_hash, skill_condition_verified="FAILED")
            if oracle is None:
                return _ArmExecution(arm, case.case_id, "completed", "unknown", {"verdict": "unknown", "reason": "independent_oracle_not_configured"}, artifact_hash, case.task_input_hash, duration, digest(output), ("independent_oracle_not_configured",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, artifact_prelaunch_hash=artifact_hash, artifact_runtime_hash=runtime_hash, artifact_postrun_hash=runtime_hash, skill_condition_verified="VERIFIED")
            try:
                oracle_value = oracle(case, output)
            except Exception as exc:
                return _ArmExecution(arm, case.case_id, "failed", "unknown", {"verdict": "unknown", "reason": "oracle_error", "error_type": type(exc).__name__}, artifact_hash, case.task_input_hash, duration, digest(output), ("oracle_error",), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, artifact_prelaunch_hash=artifact_hash, artifact_runtime_hash=runtime_hash, artifact_postrun_hash=runtime_hash, skill_condition_verified="VERIFIED")
            oracle_result = dict(oracle_value) if isinstance(oracle_value, Mapping) else {"passed": bool(oracle_value)}
            task_result = "success" if oracle_result.get("passed") is True or oracle_result.get("task_result") == "success" else "failed"
            return _ArmExecution(arm, case.case_id, "completed", task_result, oracle_result, artifact_hash, case.task_input_hash, duration, digest(output), (), "FIXTURE_EXECUTED", task_result, environment_fingerprint=environment_fingerprint, artifact_prelaunch_hash=artifact_hash, artifact_runtime_hash=runtime_hash, artifact_postrun_hash=runtime_hash, skill_condition_verified="VERIFIED")
        except subprocess.TimeoutExpired:
            return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {"verdict": "unknown", "reason": "runner_timeout"}, artifact_hash, case.task_input_hash, None, "", ("runner_timeout",), "BLOCKED_ENV", environment_fingerprint=environment_fingerprint, skill_condition_verified="UNKNOWN")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _ArmExecution(arm, case.case_id, "failed", "unknown", {"verdict": "unknown", "reason": "fixture_error", "error_type": type(exc).__name__}, artifact_hash, case.task_input_hash, None, "", (type(exc).__name__,), "FIXTURE_EXECUTED", environment_fingerprint=environment_fingerprint, skill_condition_verified="UNKNOWN")


class RealCodexThreeArmRunner:
    """Drive the existing CodexRealTaskRunner for an evidence-first pilot."""

    runner_id = "shutu.real-codex-three-arm-runner"
    runner_version = "1"

    def __init__(
        self,
        workspace_or_db: str | Path | M1Database,
        *,
        codex_command: str = "codex",
        timeout_seconds: float = 120.0,
        model_id: str = "",
        sandbox: str = "read-only",
        environment_fingerprint: str = "",
    ) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)
        self.codex_command = codex_command
        self.timeout_seconds = timeout_seconds
        self.model_id = model_id
        self.sandbox = sandbox
        self.environment_fingerprint = environment_fingerprint or digest(
            {"runner": self.runner_id, "runner_version": self.runner_version, "sandbox": sandbox}
        )[:24]

    @staticmethod
    def _artifact_hash(root: Path) -> str:
        from .release.artifacts import artifact_tree_hash

        return artifact_tree_hash(root)

    @staticmethod
    def _validate(cases: Sequence[QualityCase], artifacts: Mapping[str, str | Path]) -> dict[str, tuple[Path, str]]:
        if set(artifacts) != {"no_skill", "baseline", "candidate"}:
            raise ValueError("real three-arm artifacts must contain no_skill, baseline, and candidate")
        if not cases or len({case.case_id for case in cases}) != len(cases):
            raise ValueError("real three-arm cases must be non-empty and have unique case_id")
        if not {"target", "normal-regression", "holdout"}.issubset({case.category for case in cases}):
            raise ValueError("cases must include target, normal-regression, and holdout")
        frozen: dict[str, tuple[Path, str]] = {}
        for arm, value in artifacts.items():
            root = Path(value).expanduser().resolve()
            if not root.is_dir():
                raise ValueError(f"artifact root is missing: {arm}")
            frozen[arm] = (root, RealCodexThreeArmRunner._artifact_hash(root))
        no_skill_files = [item for item in frozen["no_skill"][0].rglob("SKILL.md") if not item.is_symlink()]
        if no_skill_files:
            raise ValueError("no_skill arm still contains a Skill artifact")
        if frozen["baseline"][1] == frozen["candidate"][1]:
            raise ValueError("baseline and candidate artifacts must have different frozen hashes")
        return frozen

    @staticmethod
    def _isolated_home(value: str | Path | None) -> Path | None:
        if value is None:
            return None
        home = Path(value).expanduser().resolve()
        user_home = Path.home().resolve()
        if home in {user_home, user_home / ".codex"}:
            return None
        return home

    @staticmethod
    def _skill_name(skill_file: Path) -> str:
        try:
            for line in skill_file.read_text(encoding="utf-8").splitlines():
                if line.strip().casefold().startswith("name:"):
                    value = line.split(":", 1)[1].strip().strip("'\"")
                    if value:
                        return value
        except (OSError, UnicodeError):
            pass
        return skill_file.parent.name

    @classmethod
    def _effective_skill_inventory(
        cls,
        root: Path,
        workspace: Path,
        codex_home: Path | None,
        target_skill_name: str = "",
    ) -> tuple[dict[str, Any], ...]:
        """Scan only candidate Skill roots; this is not native Host truth."""

        roots: list[tuple[Path, str, str]] = []
        seen_roots: set[Path] = set()

        def add(path: Path, scope: str, source_kind: str) -> None:
            try:
                resolved = path.resolve()
            except OSError:
                return
            if resolved in seen_roots or not resolved.is_dir():
                return
            seen_roots.add(resolved)
            roots.append((resolved, scope, source_kind))

        for parent in (workspace, *workspace.parents):
            add(parent / ".agents" / "skills", "project" if parent == workspace else "global", "agents_skills")
        if codex_home is not None:
            add(codex_home / "skills", "global", "codex_home_skills")
            add(codex_home / ".agents" / "skills", "global", "codex_home_agents_skills")
            add(codex_home / "plugins", "plugin", "codex_home_plugin")
        user_home = Path.home().resolve()
        add(user_home / ".agents" / "skills", "global", "user_agents_skills")
        add(user_home / ".codex" / "skills", "global", "user_codex_skills")

        entries: list[dict[str, Any]] = []
        seen_files: set[Path] = set()
        for scan_root, scope, source_kind in roots:
            try:
                if source_kind == "codex_home_plugin":
                    # Plugins may nest skills, but keep the probe bounded.
                    skill_files = sorted(scan_root.rglob("SKILL.md"), key=lambda item: item.as_posix().casefold())[:256]
                else:
                    skill_files = [scan_root / "SKILL.md"]
                    skill_files.extend(
                        item / "SKILL.md"
                        for item in sorted(scan_root.iterdir(), key=lambda value: value.as_posix().casefold())
                        if item.is_dir()
                    )
            except OSError:
                continue
            for skill_file in skill_files:
                try:
                    if skill_file in seen_files or skill_file.is_symlink() or not skill_file.is_file():
                        continue
                    skill_file = skill_file.resolve()
                    skill_file.relative_to(scan_root)
                    skill_root = skill_file.parent
                    name = cls._skill_name(skill_file)
                    if target_skill_name and name != target_skill_name:
                        content_hash = digest({"skill_md_sha256": hashlib.sha256(skill_file.read_bytes()).hexdigest()})
                        content_hash_scope = "skill_md_only"
                    else:
                        content_hash = cls._artifact_hash(skill_root)
                        content_hash_scope = "skill_tree"
                except (OSError, ValueError):
                    continue
                seen_files.add(skill_file)
                relative = skill_file.relative_to(scan_root).parent.as_posix()
                entries.append(
                    {
                        "name": name,
                        "scope": scope,
                        "source_kind": source_kind,
                        "path_digest": digest({"scope": scope, "relative": relative}),
                        "content_hash": content_hash,
                        "content_hash_scope": content_hash_scope,
                        "version_id": "skill-version-" + digest({"name": name, "content_hash": content_hash})[:24],
                        "host_detection": "filesystem_scan",
                        "host_observed": False,
                        "observed_at": utc_now(),
                    }
                )
        return tuple(entries)

    @staticmethod
    def _target_skill_name(target_skill_id: str, root: Path) -> str:
        value = str(target_skill_id or "").rsplit(":", 1)[-1].strip()
        return value or root.name

    @classmethod
    def _condition_check(
        cls,
        arm: str,
        root: Path,
        expected_hash: str,
        workspace: Path,
        codex_home: str | Path | None,
        *,
        target_skill_id: str = "",
        host_skill_inventory: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
        activation_record: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        home = cls._isolated_home(codex_home)
        inventory: tuple[dict[str, Any], ...] = ()
        evidence: dict[str, Any] = {
            "inventory_evidence_tier": "UNVERIFIED",
            "host_discovery_observed": False,
            "effective_skill_inventory": [],
        }
        try:
            if home is None:
                return "UNVERIFIED", {**evidence, "reason": "isolated_codex_home_required"}
            if cls._artifact_hash(root) != expected_hash:
                return "FAILED", {**evidence, "reason": "artifact_hash_changed", "artifact_hash": expected_hash}
            if activation_record:
                for key in ("actual_prelaunch_hash", "actual_runtime_hash_or_unknown", "actual_postrun_hash_or_unknown"):
                    if key not in activation_record:
                        continue
                    value = str(activation_record.get(key) or "")
                    if value in {"", "unknown"}:
                        return "FAILED", {**evidence, "reason": "artifact_hash_unavailable", "hash_field": key}
                    if value != expected_hash:
                        return "FAILED", {**evidence, "reason": "artifact_hash_changed", "hash_field": key}
            target_name = cls._target_skill_name(target_skill_id, root)
            inventory = cls._effective_skill_inventory(root, workspace, home, target_name)
            same_name = tuple(item for item in inventory if item["name"] == target_name)
            evidence = {
                **evidence,
                "effective_skill_inventory": list(inventory),
                "target_skill_name": target_name,
                "inventory_evidence_tier": "STATIC_CONFIG_VERIFIED",
            }
            if arm == "no_skill":
                if same_name:
                    return "FAILED", {**evidence, "reason": "no_skill_skill_leak"}
            else:
                if not same_name:
                    return "UNVERIFIED", {**evidence, "reason": "target_skill_not_in_effective_discovery_scope"}
                if len(same_name) != 1:
                    return "FAILED", {**evidence, "reason": "skill_version_or_scope_collision"}
                if same_name[0]["content_hash"] != expected_hash:
                    return "FAILED", {**evidence, "reason": "deployed_skill_hash_mismatch"}

            host_entries: Sequence[Mapping[str, Any]] = ()
            if isinstance(host_skill_inventory, Mapping):
                value = host_skill_inventory.get("entries", ())
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    host_entries = tuple(item for item in value if isinstance(item, Mapping))
            elif isinstance(host_skill_inventory, Sequence) and not isinstance(host_skill_inventory, (str, bytes)):
                host_entries = tuple(item for item in host_skill_inventory if isinstance(item, Mapping))
            host_tier = str((host_skill_inventory or {}).get("evidence_tier", "") if isinstance(host_skill_inventory, Mapping) else "")
            host_observed = host_tier == "HOST_OBSERVED" and bool(host_entries)
            if host_observed:
                host_same_name = tuple(item for item in host_entries if str(item.get("name", "")) == target_name)
                if arm == "no_skill" and host_same_name:
                    return "FAILED", {**evidence, "reason": "host_skill_leak", "host_discovery_observed": True}
                if arm != "no_skill" and (
                    len(host_same_name) != 1
                    or str(host_same_name[0].get("content_hash", "")) != expected_hash
                ):
                    return "FAILED", {**evidence, "reason": "host_discovery_does_not_match_artifact", "host_discovery_observed": True}
                if arm != "no_skill":
                    static_entry = same_name[0]
                    for key in ("scope", "version_id"):
                        reported = str(host_same_name[0].get(key, ""))
                        if reported and reported != str(static_entry.get(key, "")):
                            return "FAILED", {**evidence, "reason": "host_discovery_scope_or_version_mismatch", "host_discovery_observed": True}
                return "VERIFIED", {
                    **evidence,
                    "inventory_evidence_tier": "HOST_OBSERVED",
                    "host_discovery_observed": True,
                }
            return "STATIC_CONFIG_VERIFIED", {**evidence, "reason": "host_skill_discovery_not_observable"}
        except (OSError, ValueError):
            return "UNVERIFIED", {**evidence, "reason": "skill_inventory_unreadable"}

    @classmethod
    def _condition_verified(
        cls,
        arm: str,
        root: Path,
        expected_hash: str,
        workspace: Path,
        codex_home: str | Path | None,
        **kwargs: Any,
    ) -> str:
        return cls._condition_check(arm, root, expected_hash, workspace, codex_home, **kwargs)[0]

    @staticmethod
    def _task_result(value: Mapping[str, Any]) -> str:
        verdict = str(value.get("verdict") or value.get("status") or "").casefold()
        if value.get("passed") is True or value.get("task_result") == "success" or verdict in {"pass", "passed", "success", "ok"}:
            return "success"
        if value.get("passed") is False or value.get("task_result") == "failed" or verdict in {"fail", "failed", "error"}:
            return "failed"
        return "unknown"

    def run(
        self,
        cases: Sequence[QualityCase],
        artifacts: Mapping[str, str | Path],
        oracle: Callable[[QualityCase, Path, Mapping[str, Any]], Mapping[str, Any] | bool] | None,
        *,
        experiment_id: str = "",
        pair_id: str = "",
        workspaces: Mapping[str, str | Path] | None = None,
        codex_homes: Mapping[str, str | Path] | None = None,
        host_skill_inventory: Mapping[str, Mapping[str, Any] | Sequence[Mapping[str, Any]]] | None = None,
        persist: "QualityService | None" = None,
    ) -> QualityReport:
        from .adapters.real_agent import CodexRealTaskRunner

        frozen = self._validate(cases, artifacts)
        experiment_id = experiment_id or new_id("m74-real-skill")
        pair_id = pair_id or new_id("m74-pair")
        resolved_homes = {
            arm: self._isolated_home((codex_homes or {}).get(arm))
            for arm in ("no_skill", "baseline", "candidate")
        }
        supplied_homes = [str(home) for home in resolved_homes.values() if home is not None]
        shared_homes = len(supplied_homes) != len(set(supplied_homes))
        results: list[_ArmExecution] = []
        evidence: list[QualityEvidence] = []
        for case in cases:
            for arm in ("no_skill", "baseline", "candidate"):
                root, artifact_hash = frozen[arm]
                workspace = Path((workspaces or {}).get(arm, root)).expanduser().resolve()
                home = resolved_homes[arm]
                arm_host_inventory = (host_skill_inventory or {}).get(arm)
                runner = CodexRealTaskRunner(
                    self.db,
                    codex_command=self.codex_command,
                    codex_home=home,
                    workspace_id=f"{experiment_id}:{arm}",
                    environment_fingerprint=self.environment_fingerprint,
                    timeout=self.timeout_seconds,
                    cwd=workspace,
                    model_id=self.model_id,
                    sandbox=self.sandbox,
                )
                run_evidence = None
                condition = "UNVERIFIED"
                condition_evidence: dict[str, Any] = {"reason": "runner_not_started", "effective_skill_inventory": []}
                runner_error_status = "blocked_env"
                runner_error_type = "isolated_codex_home_required" if home is None else "shared_isolated_home" if shared_homes else "runner_not_started"
                if home is not None and not shared_homes:
                    try:
                        run_evidence = runner.run(
                            case_id=case.case_id,
                            task_input=case.runner_input(),
                            baseline_or_candidate=arm,
                            artifact_hash=artifact_hash,
                            capability_version_id=case.target_skill_id,
                            pair_id=pair_id,
                            experiment_id=experiment_id,
                            task_category=case.category,
                            artifact_root=root,
                            codex_home_override=home,
                            oracle=None,
                        )
                        condition, condition_evidence = self._condition_check(
                            arm,
                            root,
                            artifact_hash,
                            workspace,
                            home,
                            target_skill_id=case.target_skill_id,
                            host_skill_inventory=arm_host_inventory,
                            activation_record=run_evidence.activation_record,
                        )
                    except Exception as exc:
                        condition = "UNKNOWN"
                        condition_evidence = {"reason": "runner_error", "effective_skill_inventory": []}
                        runner_error_type = type(exc).__name__
                        runner_error_status = "blocked_env" if isinstance(exc, OSError) else "failed"
                elif shared_homes:
                    runner_error_type = "shared_isolated_home"
                    condition_evidence = {"reason": runner_error_type, "effective_skill_inventory": []}
                oracle_result: dict[str, Any] = {"verdict": "unknown", "reason": "execution_not_completed"}
                task_result = "unknown"
                if run_evidence is not None and run_evidence.execution_status == "completed" and condition != "VERIFIED":
                    oracle_result = {"verdict": "unknown", "reason": "condition_unverified", "condition_status": condition}
                elif run_evidence is not None and run_evidence.execution_status == "completed" and oracle is not None:
                    try:
                        value = oracle(case, workspace, run_evidence.to_dict())
                        oracle_result = dict(value) if isinstance(value, Mapping) else {"passed": bool(value)}
                        task_result = self._task_result(oracle_result)
                    except Exception as exc:
                        oracle_result = {"verdict": "unknown", "reason": "oracle_error", "error_type": type(exc).__name__}
                elif run_evidence is not None and run_evidence.execution_status != "completed":
                    oracle_result = {"verdict": "unknown", "reason": "execution_not_completed"}
                elif run_evidence is not None and oracle is None:
                    oracle_result = {"verdict": "unknown", "reason": "independent_oracle_not_configured"}
                else:
                    oracle_result = {"verdict": "unknown", "reason": "runner_error", "error_type": runner_error_type}
                if run_evidence is None:
                    item = _ArmExecution(
                        arm, case.case_id, runner_error_status, "unknown", oracle_result, artifact_hash, case.task_input_hash,
                        None, "", ("runner_error",), "REAL_AGENT_OBSERVED", environment_fingerprint=self.environment_fingerprint,
                        skill_condition_verified=condition, condition_reason=str(condition_evidence.get("reason") or runner_error_type),
                        effective_skill_inventory=tuple(condition_evidence.get("effective_skill_inventory", ())),
                    )
                else:
                    activation = run_evidence.activation_record or {}
                    item = _ArmExecution(
                        arm=arm,
                        case_id=case.case_id,
                        execution_status=run_evidence.execution_status,
                        task_result=task_result,
                        oracle_result=oracle_result,
                        artifact_hash=artifact_hash,
                        task_input_hash=case.task_input_hash,
                        duration_ms=(run_evidence.metrics or {}).get("walltime_ms"),
                        output_digest=run_evidence.session_run_digest,
                        errors=(run_evidence.failure_layer,) if run_evidence.failure_layer else (),
                        evidence_tier=run_evidence.execution_evidence_tier,
                        execution_verdict="completed" if run_evidence.execution_status == "completed" else "unknown",
                        runner_id=run_evidence.runner_id,
                        runner_version=run_evidence.runner_version,
                        host_version=run_evidence.host_version,
                        model_id=run_evidence.model_id,
                        environment_fingerprint=run_evidence.environment_fingerprint,
                        execution_manifest_hash=run_evidence.execution_manifest_hash,
                        trace_id=run_evidence.trace_ids[0] if run_evidence.trace_ids else "",
                        event_refs=run_evidence.event_refs,
                        artifact_prelaunch_hash=str(activation.get("actual_prelaunch_hash") or "unknown"),
                        artifact_runtime_hash=str(activation.get("actual_runtime_hash_or_unknown") or "unknown"),
                        artifact_postrun_hash=str(activation.get("actual_postrun_hash_or_unknown") or "unknown"),
                        skill_condition_verified=condition,
                        skill_invocation_observed="NOT_OBSERVABLE",
                        condition_reason=str(condition_evidence.get("reason") or ""),
                        effective_skill_inventory=tuple(condition_evidence.get("effective_skill_inventory", ())),
                        evidence=run_evidence.to_dict(),
                    )
                    if task_result != "unknown":
                        item = _replace_arm(item, execution_verdict=task_result, oracle_result=oracle_result)
                results.append(item)
                evidence.append(
                    QualityEvidence(
                        capability_kind="skill",
                        capability_id=case.target_skill_id or f"skill-arm:{arm}",
                        task_case_id=case.case_id,
                        host="codex",
                        model_provider="codex-cli",
                        model_id=item.model_id,
                        trace_id=item.trace_id or None,
                        event_refs=item.event_refs,
                        evidence_tier=item.evidence_tier,
                        selection_mode="DISABLED" if arm == "no_skill" else "FORCED",
                        observation_completeness="complete" if item.execution_status == "completed" else "partial",
                        oracle_id="external-oracle" if oracle else None,
                        oracle_version="1" if oracle else None,
                        # A per-arm record cannot carry a cross-arm improvement verdict.
                        verdict="INCONCLUSIVE",
                        metrics={
                            "arm": arm,
                            "execution_verdict": item.execution_verdict,
                            "oracle_verdict": oracle_result.get("verdict", oracle_result.get("passed", "unknown")),
                            "skill_condition_verified": item.skill_condition_verified,
                            "skill_invocation_observed": item.skill_invocation_observed,
                            "execution_manifest_hash": item.execution_manifest_hash,
                            "artifact_prelaunch_hash": item.artifact_prelaunch_hash,
                            "artifact_runtime_hash": item.artifact_runtime_hash,
                            "artifact_postrun_hash": item.artifact_postrun_hash,
                            "condition_reason": item.condition_reason,
                            "effective_skill_inventory": list(item.effective_skill_inventory),
                            "comparison_verdict": "N/A",
                        },
                        environment_fingerprint=item.environment_fingerprint,
                        artifact_hash=artifact_hash,
                        correlation_quality="exact" if item.trace_id else "unknown",
                        unknown_fields=("skill_lifecycle", "tokens", "cost"),
                    )
                )
        baseline = {item.case_id: item for item in results if item.arm == "baseline"}
        candidate = {item.case_id: item for item in results if item.arm == "candidate"}
        unknown = [item.case_id for item in results if item.task_result == "unknown" or item.execution_status != "completed"]
        regressions = sorted(
            case_id for case_id in baseline
            if baseline[case_id].task_result == "success" and candidate[case_id].task_result != "success"
        )
        holdout_ids = {case.case_id for case in cases if case.holdout or case.category == "holdout"}
        holdout_regressions = sorted(case_id for case_id in regressions if case_id in holdout_ids)
        unknown_samples = [
            {
                "case_id": item.case_id,
                "arm": item.arm,
                "reason": item.oracle_result.get("reason", "unknown") or "unknown",
            }
            for item in results
            if item.task_result == "unknown" or item.execution_status != "completed"
        ]
        if unknown:
            benefit = "BLOCKED_ENV" if any(item.execution_status == "blocked_env" for item in results) else "INCONCLUSIVE"
        elif regressions:
            benefit = "REGRESSED"
        else:
            baseline_success = sum(item.task_result == "success" for item in baseline.values())
            candidate_success = sum(item.task_result == "success" for item in candidate.values())
            benefit = "IMPROVED" if candidate_success > baseline_success else "EQUIVALENT"
        planned = len(cases) * 3
        attempted = sum(bool(item.trace_id) for item in results)
        completed = sum(item.execution_status == "completed" for item in results)
        oracle_verified = sum(item.task_result in {"success", "failed"} for item in results)
        condition_verified = all(item.skill_condition_verified == "VERIFIED" for item in results)
        pilot = "PASS" if completed == attempted and oracle_verified == attempted and condition_verified else "BLOCKED_ENV" if any(item.execution_status == "blocked_env" for item in results) else "INCONCLUSIVE"
        report = QualityReport(
            report_id=new_id("quality-real-three-arm"),
            report_kind="skill_real_three_arm",
            status="PASS" if pilot == "PASS" else "PARTIAL",
            generated_at=utc_now(),
            environment_fingerprint=self.environment_fingerprint,
            summary={
                "experiment_id": experiment_id,
                "pair_id": pair_id,
                "runner_id": self.runner_id,
                "runner_version": self.runner_version,
                "pilot_status": pilot,
                "verdict": benefit,
                "pilot_suite_condition_effect": f"PILOT_SUITE_{benefit}" if pilot == "PASS" and benefit == "IMPROVED" else benefit,
                "production_benefit_verdict": "UNVERIFIED",
                "real_three_arm_cases_planned": planned,
                "real_three_arm_cases_attempted": attempted,
                "real_three_arm_cases_completed": completed,
                "real_three_arm_oracle_verified": oracle_verified,
                "suite_id": experiment_id,
                "case_ids": [case.case_id for case in cases],
                "sample_size": len(cases),
                "unknown_case_ids": sorted(set(unknown)),
                "unknown_samples": unknown_samples,
                "regressions": regressions,
                "holdout_regressions": holdout_regressions,
                "skill_condition_verified": "PASS" if condition_verified else "PARTIAL" if all(item.skill_condition_verified in {"VERIFIED", "STATIC_CONFIG_VERIFIED"} for item in results) else "UNVERIFIED",
                "skill_condition_status_counts": {
                    state: sum(item.skill_condition_verified == state for item in results)
                    for state in ("VERIFIED", "STATIC_CONFIG_VERIFIED", "FAILED", "UNVERIFIED", "UNKNOWN")
                },
                "skill_invocation_observed": "NOT_OBSERVABLE",
                "oracle_independence": "PASS" if oracle is not None else "NOT_CONFIGURED",
                "artifact_freeze": "PASS" if all(item.artifact_postrun_hash == item.artifact_hash for item in results) else "FAIL",
                "execution_verdict": {state: sum(item.execution_verdict == state for item in results) for state in ("success", "failed", "unknown", "completed")},
            },
            metrics=(
                QualityMetric("real_cases_planned", planned, "real_codex_runner", planned),
                QualityMetric("real_cases_attempted", attempted, "real_codex_runner", planned),
                QualityMetric("real_cases_completed", completed, "real_codex_runner", planned, "provider_or_host_blocked" if completed != planned else ""),
                QualityMetric("oracle_verified", oracle_verified, "independent_oracle", planned, "oracle_or_execution_unknown" if oracle_verified != planned else ""),
                QualityMetric("task_success_rate.baseline", _rate(results, "baseline"), "independent_oracle", len(cases), "unknown_execution" if unknown else ""),
                QualityMetric("task_success_rate.candidate", _rate(results, "candidate"), "independent_oracle", len(cases), "unknown_execution" if unknown else ""),
                QualityMetric("tool_call_count", "N/A", "codex_host_not_comparable", 0, "host_usage_not_exposed"),
                QualityMetric("token_count", "N/A", "codex_host_not_comparable", 0, "provider_usage_not_exposed"),
            ),
            evidence=tuple(evidence),
            results=tuple(item.to_dict() for item in results),
            missing_fields=("skill_lifecycle", "tokens", "cost", "desktop_events"),
        )
        if persist:
            persist.save(report)
        return report


def _rate(results: Sequence[_ArmExecution], arm: str) -> float | str:
    values = [item for item in results if item.arm == arm]
    if not values or any(item.task_result == "unknown" for item in values):
        return "N/A"
    return sum(item.task_result == "success" for item in values) / len(values)


def _replace_arm(item: _ArmExecution, **changes: Any) -> _ArmExecution:
    return replace(item, **changes)


class JointAttribution:
    """Create cautious joint ownership findings; weak joins stay unknown."""

    def classify(
        self,
        *,
        target_kind: str,
        target_id: str,
        classification: str,
        primary_owner: str = "unknown",
        contributing_owners: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        correlation_quality: str = "unknown",
        verified_bridge: bool = False,
        required_verification: Sequence[str] = (),
    ) -> QualityFinding:
        owners = tuple(dict.fromkeys(contributing_owners))
        reliable = correlation_quality == "exact" and verified_bridge
        if primary_owner not in OWNERS or (primary_owner == "skill" and not reliable):
            primary_owner = "unknown"
        if not reliable and len(owners) > 1:
            primary_owner = "unknown"
        missing = tuple(required_verification)
        if primary_owner == "unknown" and not missing:
            missing = ("verified_skill_mcp_or_agent_bridge",)
        return QualityFinding(
            finding_id=new_id("quality-finding"),
            target_kind=target_kind,
            target_id=target_id,
            classification=classification if primary_owner != "unknown" else "UNVERIFIED_HYPOTHESIS",
            primary_owner=primary_owner,
            contributing_owners=owners,
            confidence="high" if reliable and primary_owner != "unknown" else "unknown",
            evidence_refs=tuple(evidence_refs),
            required_verification=missing,
        )


class QualityService:
    """Persist and query bounded reports in the shared M1 database."""

    def __init__(self, workspace_or_db: str | Path | M1Database) -> None:
        self.db = workspace_or_db if isinstance(workspace_or_db, M1Database) else M1Database(workspace_or_db)

    def save(self, report: QualityReport) -> QualityReport:
        payload = report.to_dict()
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO quality_runs(run_id, report_kind, status, environment_fingerprint, report_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (report.report_id, report.report_kind, report.status, report.environment_fingerprint, _canonical(payload), report.generated_at),
            )
            connection.execute("DELETE FROM quality_evidence WHERE run_id = ?", (report.report_id,))
            for index, evidence in enumerate(report.evidence):
                value = evidence.to_dict()
                connection.execute(
                    "INSERT INTO quality_evidence(evidence_id, run_id, capability_kind, capability_id, capability_version_id, task_case_id, evidence_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"{report.report_id}:e{index}", report.report_id, evidence.capability_kind, evidence.capability_id, evidence.capability_version_id or "", evidence.task_case_id or "", _canonical(value)),
                )
        return report

    def latest(self, *, report_kind: str = "") -> dict[str, Any] | None:
        query = "SELECT report_json FROM quality_runs"
        values: list[Any] = []
        if report_kind:
            query += " WHERE report_kind = ?"
            values.append(report_kind)
        query += " ORDER BY created_at DESC, run_id DESC LIMIT 1"
        with self.db.connection() as connection:
            row = connection.execute(query, values).fetchone()
        return json.loads(row[0]) if row else None

    def summary(self, *, capability_id: str = "", report_kind: str = "") -> dict[str, Any]:
        reports = self._reports(report_kind=report_kind)
        if capability_id:
            reports = [item for item in reports if capability_id in _canonical(item)]
        if not reports:
            return {"status": "NO_DATA", "data_status": "NO_DATA", "source": "quality_runs", "summary": None, "reports": []}
        metric_values: dict[str, list[Any]] = {}
        missing: set[str] = set()
        evidence_tiers: dict[str, int] = {}
        unknown_count = 0
        for report in reports:
            for metric in report.get("metrics", []):
                metric_values.setdefault(str(metric.get("name")), []).append(metric.get("value"))
            missing.update(str(value) for value in report.get("missing_fields", []))
            for item in report.get("evidence", []):
                tier = str(item.get("evidence_tier") or "UNKNOWN")
                evidence_tiers[tier] = evidence_tiers.get(tier, 0) + 1
            unknown_count += len(report.get("summary", {}).get("unknown_case_ids", []))
        return {
            "status": "PASS",
            "data_status": "OBSERVED",
            "source": "quality_runs",
            "summary": {
                "report_count": len(reports),
                "metric_values": metric_values,
                "missing_fields": sorted(missing),
                "evidence_tier_counts": evidence_tiers,
                "unknown_case_count": unknown_count,
                "production_benefit_verdicts": [item.get("summary", {}).get("production_benefit_verdict", item.get("summary", {}).get("verdict", "INCONCLUSIVE")) for item in reports],
            },
            "reports": [{"report_id": item.get("report_id"), "report_kind": item.get("report_kind"), "status": item.get("status"), "generated_at": item.get("generated_at"), "summary": item.get("summary", {})} for item in reports],
            "recommendations": [] if not missing else [{"type": "verification", "required": sorted(missing)}],
        }

    def detail(self, report_id: str = "", *, capability_id: str = "") -> dict[str, Any]:
        report = None
        if report_id:
            with self.db.connection() as connection:
                row = connection.execute("SELECT report_json FROM quality_runs WHERE run_id = ?", (report_id,)).fetchone()
            report = json.loads(row[0]) if row else None
        else:
            report = self.latest()
        if report is None:
            return {"status": "NO_DATA", "data_status": "NO_DATA", "source": "quality_runs", "report": None}
        if capability_id and capability_id not in _canonical(report):
            return {"status": "NO_DATA", "data_status": "NO_DATA", "source": "quality_runs", "report": None}
        return {"status": "PASS", "data_status": "OBSERVED", "source": "quality_runs", "report": report}

    def _reports(self, *, report_kind: str = "") -> list[dict[str, Any]]:
        query = "SELECT report_json FROM quality_runs"
        values: list[Any] = []
        if report_kind:
            query += " WHERE report_kind = ?"
            values.append(report_kind)
        query += " ORDER BY created_at DESC, run_id DESC LIMIT 100"
        with self.db.connection() as connection:
            return [json.loads(row[0]) for row in connection.execute(query, values).fetchall()]


def _tree_hash(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix().casefold()):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            raw = item.read_bytes()
            entries.append({"path": item.relative_to(root).as_posix(), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        except OSError:
            entries.append({"path": item.relative_to(root).as_posix(), "unreadable": True})
    return digest(entries)


__all__ = [
    "EVIDENCE_TIERS",
    "JointAttribution",
    "OWNERS",
    "QualityCase",
    "QualityEvidence",
    "QualityFinding",
    "QualityMetric",
    "QualityReport",
    "QualityService",
    "SELECTION_MODES",
    "SkillObservation",
    "SkillRegistry",
    "SkillRoutingEvaluator",
    "SkillSelectionLabel",
    "RealCodexThreeArmRunner",
    "ThreeArmQualityRunner",
    "digest",
]
