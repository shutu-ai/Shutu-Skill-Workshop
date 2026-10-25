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
from dataclasses import asdict, dataclass, field
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

    def __post_init__(self) -> None:
        if self.should_use not in {"yes", "no", "review"}:
            raise ValueError("should_use must be yes, no, or review")
        if self.selection_mode not in SELECTION_MODES:
            raise ValueError("unsupported selection mode")
        if self.observation_evidence_tier not in EVIDENCE_TIERS:
            raise ValueError("unsupported observation evidence tier")

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

    def evaluate(self, labels: Sequence[SkillSelectionLabel], *, environment_fingerprint: str = "") -> QualityReport:
        if len({label.case_id for label in labels}) != len(labels):
            raise ValueError("routing case_id must be unique")
        natural = [label for label in labels if label.selection_mode == "NATURAL"]
        forced = [label for label in labels if label.selection_mode == "FORCED"]
        usable = [label for label in natural if label.should_use in {"yes", "no"} and label.observed_invocation is not None]
        unknown = [label.case_id for label in natural if label not in usable]
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for label in usable:
            if label.should_use == "yes" and label.observed_invocation:
                counts["tp"] += 1
            elif label.should_use == "no" and label.observed_invocation:
                counts["fp"] += 1
            elif label.should_use == "yes":
                counts["fn"] += 1
            else:
                counts["tn"] += 1
        metrics: list[QualityMetric] = []
        if len(usable) == len(natural) and natural:
            precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
            recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            for name, value in (("precision", precision), ("recall", recall), ("f1", f1)):
                metrics.append(QualityMetric(name, value, "host_observed_skill_invocation", len(natural)))
            status = "PASS"
        else:
            for name in ("precision", "recall", "f1"):
                metrics.append(QualityMetric(name, "N/A", "not_observable", 0, "host_skill_invocation_not_exposed"))
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
                "forced_condition": "FORCED_SKILL_CONDITION" if forced else "",
                "selection_truth": "HOST_OBSERVED" if len(usable) == len(natural) and natural else "NOT_OBSERVABLE",
            },
            metrics=tuple(metrics),
            results=results,
            missing_fields=("skill_actual_invocation_observed",) if unknown else (),
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

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = list(self.errors)
        return value


class ThreeArmQualityRunner:
    """Run isolated A/B/C artifacts and judge them with an external Oracle."""

    runner_id = "shutu.skill-three-arm-runner"
    runner_version = "1"

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        cases: Sequence[QualityCase],
        artifacts: Mapping[str, str | Path],
        oracle: Callable[[QualityCase, Any], Mapping[str, Any] | bool],
        *,
        oracle_id: str = "quality-oracle",
        oracle_version: str = "1",
        environment_fingerprint: str = "",
        persist: "QualityService | None" = None,
    ) -> QualityReport:
        required = {"no_skill", "baseline", "candidate"}
        if set(artifacts) != required:
            raise ValueError("three-arm artifacts must contain no_skill, baseline, and candidate")
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
                results.append(self._execute(arm, case, *frozen[arm], oracle=oracle, environment_fingerprint=env))
        result_dicts = tuple(item.to_dict() for item in results)
        baseline = {item.case_id: item for item in results if item.arm == "baseline"}
        candidate = {item.case_id: item for item in results if item.arm == "candidate"}
        unknown = [item.case_id for item in results if item.task_result == "unknown"]
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
                verdict=verdict,
                metrics={"task_result": item.task_result, "duration_ms": item.duration_ms},
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

    def _execute(
        self,
        arm: str,
        case: QualityCase,
        root: Path,
        artifact_hash: str,
        *,
        oracle: Callable[[QualityCase, Any], Mapping[str, Any] | bool],
        environment_fingerprint: str,
    ) -> _ArmExecution:
        del environment_fingerprint
        validator = root / "scripts" / "validator.py"
        started = time.perf_counter()
        if not validator.is_file():
            return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {}, artifact_hash, case.task_input_hash, None, "", ("validator_missing",), "BLOCKED_ENV")
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
                return _ArmExecution(arm, case.case_id, "failed", "unknown", {}, artifact_hash, case.task_input_hash, duration, "", (f"runner_exit_{completed.returncode}",))
            output = json.loads(completed.stdout)
            if _tree_hash(root) != artifact_hash:
                return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {}, artifact_hash, case.task_input_hash, duration, "", ("artifact_mutated_during_run",), "BLOCKED_ENV")
            oracle_value = oracle(case, output)
            oracle_result = dict(oracle_value) if isinstance(oracle_value, Mapping) else {"passed": bool(oracle_value)}
            task_result = "success" if oracle_result.get("passed") is True or oracle_result.get("task_result") == "success" else "failed"
            return _ArmExecution(arm, case.case_id, "completed", task_result, oracle_result, artifact_hash, case.task_input_hash, duration, digest(output))
        except subprocess.TimeoutExpired:
            return _ArmExecution(arm, case.case_id, "blocked_env", "unknown", {}, artifact_hash, case.task_input_hash, None, "", ("runner_timeout",), "BLOCKED_ENV")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return _ArmExecution(arm, case.case_id, "failed", "unknown", {}, artifact_hash, case.task_input_hash, None, "", (type(exc).__name__,))


def _rate(results: Sequence[_ArmExecution], arm: str) -> float | str:
    values = [item for item in results if item.arm == arm]
    if not values or any(item.task_result == "unknown" for item in values):
        return "N/A"
    return sum(item.task_result == "success" for item in values) / len(values)


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
        for report in reports:
            for metric in report.get("metrics", []):
                metric_values.setdefault(str(metric.get("name")), []).append(metric.get("value"))
            missing.update(str(value) for value in report.get("missing_fields", []))
        return {
            "status": "PASS",
            "data_status": "OBSERVED",
            "source": "quality_runs",
            "summary": {"report_count": len(reports), "metric_values": metric_values, "missing_fields": sorted(missing)},
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
    "ThreeArmQualityRunner",
    "digest",
]
