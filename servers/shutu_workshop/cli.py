"""Small independent M1 CLI; the original SkillWorkshop CLI is unchanged."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .adapters import CodexRealTaskRunner
from .capabilities import CapabilityRegistry
from .diagnostics import DiagnosisService, ISSUE_STATUSES
from .evolution import (
    EvaluationService,
    EvolutionDiagnosisService,
    FileBackedIsolatedRunner,
    ProposalService,
    local_fixture_suite,
)
from .ingestion import TraceIngestor
from .models import Capability
from .release import (
    ArtifactManifest,
    FileArtifactService,
    FrozenComparisonManifest,
    ObservationWindow,
    OptimizationFeedbackService,
    ReleaseObservationService,
    ReleasePlanService,
    isolated_skill_suite,
)
from .traces import TraceStore


def _value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_value(item) for item in value]
    return value


def _print(value: Any) -> None:
    print(json.dumps(_value(value), ensure_ascii=False, indent=2))


def _load_manifest(workspace: Path, proposal_id: str, path: Path | None) -> ArtifactManifest:
    candidate = path
    if candidate is None:
        root = workspace / ".skill_workshop" / "m5-artifacts"
        matches = sorted(root.glob(f"{proposal_id}-*.json"))
        candidate = matches[-1] if matches else None
    if candidate is None or not candidate.is_file():
        raise SystemExit("candidate manifest does not exist; materialize an artifact first")
    try:
        return ArtifactManifest.from_dict(json.loads(candidate.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SystemExit(f"invalid candidate manifest: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shutu Skill Workshop M1 local trace store")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)

    capability = commands.add_parser("capability")
    capability_commands = capability.add_subparsers(dest="capability_command", required=True)
    capability_commands.add_parser("list")
    register = capability_commands.add_parser("register")
    register.add_argument("capability_id")
    register.add_argument("--kind", required=True, choices=sorted(Capability.KINDS))
    register.add_argument("--adapter-id", required=True)
    register.add_argument("--locator", default="")
    register.add_argument("--metadata", default="{}")
    versions = capability_commands.add_parser("versions")
    versions.add_argument("capability_id")
    snapshot = capability_commands.add_parser("snapshot")
    snapshot.add_argument("capability_id")
    snapshot.add_argument("--kind", required=True, choices=sorted(Capability.KINDS))
    snapshot.add_argument("--adapter-id", required=True)
    snapshot.add_argument("--version-id", required=True)
    snapshot.add_argument("--source", default="cli")
    snapshot.add_argument("--content-hash", default="")
    snapshot.add_argument("--schema-hash", default="")
    snapshot.add_argument("--provider-version", default="unknown")

    trace = commands.add_parser("trace")
    trace_commands = trace.add_subparsers(dest="trace_command", required=True)
    import_command = trace_commands.add_parser("import")
    import_command.add_argument("--jsonl", type=Path, required=True)
    show = trace_commands.add_parser("show")
    show.add_argument("trace_id")
    events = trace_commands.add_parser("events")
    events.add_argument("trace_id", nargs="?")
    events.add_argument("--adapter-id")
    events.add_argument("--capability-id")
    events.add_argument("--error-type")
    prune = trace_commands.add_parser("prune")
    prune.add_argument("--before", required=True, help="ISO timestamp; payloads older than it are deleted")
    prune.add_argument("--trace-id")

    diagnose = commands.add_parser("diagnose")
    diagnose_commands = diagnose.add_subparsers(dest="diagnose_command", required=True)
    diagnose_commands.add_parser("rules")
    diagnose_trace = diagnose_commands.add_parser("trace")
    diagnose_trace.add_argument("trace_id")
    diagnose_trace.add_argument("--json", action="store_true")
    diagnose_new = diagnose_commands.add_parser("new")
    diagnose_new.add_argument("--limit", type=int, default=100)
    diagnose_new.add_argument("--since")
    diagnose_new.add_argument("--json", action="store_true")

    issue = commands.add_parser("issue")
    issue_commands = issue.add_subparsers(dest="issue_command", required=True)
    issue_list = issue_commands.add_parser("list")
    issue_list.add_argument("--status", choices=sorted(ISSUE_STATUSES))
    issue_list.add_argument("--rule-id")
    issue_list.add_argument("--limit", type=int, default=100)
    issue_list.add_argument("--json", action="store_true")
    issue_show = issue_commands.add_parser("show")
    issue_show.add_argument("issue_id")
    issue_show.add_argument("--json", action="store_true")
    issue_status = issue_commands.add_parser("status")
    issue_status.add_argument("issue_id")
    issue_status.add_argument("status", choices=sorted(ISSUE_STATUSES))
    issue_status.add_argument("--expected-row-version", type=int)
    issue_status.add_argument("--reason", default="cli")

    evolution = commands.add_parser("evolution")
    evolution_commands = evolution.add_subparsers(dest="evolution_command", required=True)
    evolution_diagnose = evolution_commands.add_parser("diagnose")
    evolution_diagnose.add_argument("issue_id")
    evolution_diagnose.add_argument("--provider", default="fake")
    evolution_diagnose.add_argument("--model", default="")
    evolution_diagnose.add_argument("--force", action="store_true")
    evolution_diagnose.add_argument("--json", action="store_true")
    evolution_proposals = evolution_commands.add_parser("proposals")
    evolution_proposals.add_argument("issue_id")
    evolution_proposals.add_argument("--diagnosis-id")
    evolution_proposals.add_argument("--json", action="store_true")
    evolution_review = evolution_commands.add_parser("review")
    evolution_review.add_argument("proposal_id")
    evolution_review.add_argument("--decision", required=True)
    evolution_review.add_argument("--reviewer", default="cli")
    evolution_review.add_argument("--expected-row-version", type=int)
    evolution_review.add_argument("--json", action="store_true")

    evaluation = commands.add_parser("eval")
    evaluation_commands = evaluation.add_subparsers(dest="eval_command", required=True)
    evaluation_run = evaluation_commands.add_parser("run")
    evaluation_run.add_argument("--proposal", dest="proposal_id", required=True)
    evaluation_run.add_argument("--suite", default="m4-local-fixture-v2")
    evaluation_run.add_argument("--mode", choices=("fixture", "isolated", "real"), default="fixture")
    evaluation_run.add_argument("--baseline-root", type=Path)
    evaluation_run.add_argument("--candidate-root", type=Path)
    evaluation_run.add_argument("--json", action="store_true")
    evaluation_report = evaluation_commands.add_parser("report")
    evaluation_report.add_argument("eval_id")
    evaluation_report.add_argument("--json", action="store_true")

    release = commands.add_parser("release")
    release_commands = release.add_subparsers(dest="release_command", required=True)
    release_materialize = release_commands.add_parser("materialize")
    release_materialize.add_argument("--proposal", dest="proposal_id", required=True)
    release_materialize.add_argument("--baseline-root", type=Path, required=True)
    release_materialize.add_argument("--candidate-root", type=Path, required=True)
    release_materialize.add_argument("--isolated-root", type=Path, required=True)
    release_materialize.add_argument("--json", action="store_true")
    release_eligible = release_commands.add_parser("eligible")
    release_eligible.add_argument("--proposal", dest="proposal_id", required=True)
    release_eligible.add_argument("--evaluation", dest="evaluation_run_id", required=True)
    release_eligible.add_argument("--candidate-manifest", type=Path)
    release_eligible.add_argument("--target", type=Path)
    release_eligible.add_argument("--target-scope", default="isolated_workspace")
    release_eligible.add_argument("--approval-ref", type=Path)
    release_eligible.add_argument("--json", action="store_true")
    release_plan = release_commands.add_parser("plan")
    release_plan.add_argument("--proposal", dest="proposal_id", required=True)
    release_plan.add_argument("--evaluation", dest="evaluation_run_id", required=True)
    release_plan.add_argument("--candidate-manifest", type=Path)
    release_plan.add_argument("--isolated-root", type=Path, required=True)
    release_plan.add_argument("--skill-name", default="")
    release_plan.add_argument("--target-scope", default="isolated_workspace")
    release_plan.add_argument("--json", action="store_true")
    release_approve = release_commands.add_parser("approve")
    release_approve.add_argument("plan_id")
    release_approve.add_argument("--expected-hash", required=True)
    release_approve.add_argument("--reviewer", default="cli")
    release_approve.add_argument("--json", action="store_true")
    release_verify = release_commands.add_parser("verify")
    release_verify.add_argument("plan_id")
    release_verify.add_argument("--isolated-root", type=Path)
    release_verify.add_argument("--json", action="store_true")
    release_execute = release_commands.add_parser("execute")
    release_execute.add_argument("plan_id")
    release_execute.add_argument("--isolated-root", type=Path, required=True)
    release_execute.add_argument("--json", action="store_true")
    release_status = release_commands.add_parser("status")
    release_status.add_argument("plan_id")
    release_status.add_argument("--json", action="store_true")
    release_observe = release_commands.add_parser("observe")
    release_observe.add_argument("release_id")
    release_observe.add_argument("--before-start", required=True)
    release_observe.add_argument("--before-end", required=True)
    release_observe.add_argument("--after-start", required=True)
    release_observe.add_argument("--after-end", required=True)
    release_observe.add_argument("--min-samples", type=int, default=2)
    release_observe.add_argument("--manifest", type=Path)
    release_observe.add_argument("--manifest-id", default="")
    release_observe.add_argument("--task-category", default="")
    release_observe.add_argument("--source", default="")
    release_observe.add_argument("--environment", default="")
    release_observe.add_argument("--agent-id", default="")
    release_observe.add_argument("--runner-id", default="")
    release_observe.add_argument("--source-tier", default="")
    release_observe.add_argument("--experiment-id", default="")
    release_observe.add_argument("--unknown-max-ratio", type=float, default=0.25)
    release_observe.add_argument("--json", action="store_true")
    release_rollback = release_commands.add_parser("rollback")
    release_rollback.add_argument("release_id")
    release_rollback.add_argument("--version", required=True)
    release_rollback.add_argument("--isolated-root", type=Path, required=True)
    release_rollback.add_argument("--json", action="store_true")

    freeze_manifest = release_commands.add_parser("freeze-manifest")
    freeze_manifest.add_argument("release_id")
    freeze_manifest.add_argument("--manifest", type=Path, required=True)
    freeze_manifest.add_argument("--json", action="store_true")

    agent = commands.add_parser("agent")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_codex = agent_commands.add_parser("codex")
    agent_codex.add_argument("--case-id", required=True)
    agent_codex.add_argument("--input", help="JSON task input; it is passed only to the child process")
    agent_codex.add_argument("--input-file", type=Path, help="UTF-8 JSON task input file; avoids PowerShell native quoting")
    agent_codex.add_argument("--side", choices=("baseline", "candidate"), required=True)
    agent_codex.add_argument("--artifact-hash", required=True)
    agent_codex.add_argument("--capability-version-id", default="")
    agent_codex.add_argument("--pair-id", default="")
    agent_codex.add_argument("--experiment-id", default="")
    agent_codex.add_argument("--comparison-manifest-hash", default="")
    agent_codex.add_argument("--task-category", default="")
    agent_codex.add_argument("--agent-id", default="codex")
    agent_codex.add_argument("--model", default="")
    agent_codex.add_argument("--codex-home", type=Path)
    agent_codex.add_argument("--cwd", type=Path)
    agent_codex.add_argument("--timeout", type=float, default=120.0)
    agent_codex.add_argument("--extra-arg", action="append", default=[])
    agent_codex.add_argument("--json", action="store_true")

    feedback = commands.add_parser("feedback")
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)
    feedback_record = feedback_commands.add_parser("record")
    feedback_record.add_argument("--observation", required=True)
    feedback_record.add_argument("--issue", default="")
    feedback_record.add_argument("--diagnosis", default="")
    feedback_record.add_argument("--proposal", default="")
    feedback_record.add_argument("--evaluation", default="")
    feedback_record.add_argument("--experiment-id", default="")
    feedback_record.add_argument("--reviewed-at", default="")
    feedback_record.add_argument("--json", action="store_true")
    feedback_list = feedback_commands.add_parser("list")
    feedback_list.add_argument("--release", dest="release_id", default="")
    feedback_list.add_argument("--observation", dest="observation_id", default="")
    feedback_list.add_argument("--limit", type=int, default=100)
    feedback_list.add_argument("--json", action="store_true")
    feedback_show = feedback_commands.add_parser("show")
    feedback_show.add_argument("feedback_id")
    feedback_show.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capability":
        registry = CapabilityRegistry(args.workspace)
        if args.capability_command == "list":
            _print(registry.list())
        elif args.capability_command == "versions":
            _print(registry.list_versions(args.capability_id))
        elif args.capability_command == "snapshot":
            capability = registry.register(
                Capability(
                    capability_id=args.capability_id,
                    kind=args.kind,
                    adapter_id=args.adapter_id,
                    locator=args.capability_id,
                    discovered_from="cli",
                )
            )
            _print(
                registry.snapshot(
                    capability,
                    version_id=args.version_id,
                    source=args.source,
                    content_hash=args.content_hash,
                    schema_hash=args.schema_hash,
                    provider_version=args.provider_version,
                )
            )
        elif args.capability_command == "register":
            try:
                metadata = json.loads(args.metadata)
            except json.JSONDecodeError as exc:
                raise SystemExit("--metadata must be JSON") from exc
            if not isinstance(metadata, dict):
                raise SystemExit("--metadata must be a JSON object")
            _print(
                registry.register(
                    Capability(
                        capability_id=args.capability_id,
                        kind=args.kind,
                        adapter_id=args.adapter_id,
                        locator=args.locator,
                        metadata=metadata,
                        discovered_from="cli",
                    )
                )
            )
        return 0

    if args.command == "diagnose":
        service = DiagnosisService(args.workspace)
        if args.diagnose_command == "rules":
            _print(service.rules())
        elif args.diagnose_command == "trace":
            _print(service.analyze_trace(args.trace_id))
        elif args.diagnose_command == "new":
            _print(service.analyze_new(limit=args.limit, since=args.since))
        return 0

    if args.command == "issue":
        service = DiagnosisService(args.workspace)
        if args.issue_command == "list":
            _print(service.list_issues(status=args.status, rule_id=args.rule_id, limit=args.limit))
        elif args.issue_command == "show":
            value = service.show_issue(args.issue_id)
            if value is None:
                raise SystemExit("issue does not exist")
            _print(value)
        elif args.issue_command == "status":
            _print(
                service.set_issue_status(
                    args.issue_id,
                    args.status,
                    expected_row_version=args.expected_row_version,
                    reason=args.reason,
                )
            )
        return 0

    if args.command == "evolution":
        if args.evolution_command == "diagnose":
            _print(
                EvolutionDiagnosisService(args.workspace).diagnose(
                    args.issue_id,
                    provider=args.provider,
                    model_id=args.model,
                    force=args.force,
                )
            )
        elif args.evolution_command == "proposals":
            _print(ProposalService(args.workspace).create_from_diagnosis(args.issue_id, args.diagnosis_id))
        elif args.evolution_command == "review":
            service = ProposalService(args.workspace)
            current = service.get(args.proposal_id)
            if current is None:
                raise SystemExit("proposal does not exist")
            if args.decision.replace("-", "_") == "accept_for_eval" and current.status == "draft":
                current = service.review(
                    args.proposal_id,
                    "ready-for-review",
                    reviewer=args.reviewer,
                    expected_row_version=args.expected_row_version,
                )
                args.expected_row_version = current.row_version
            _print(
                service.review(
                    args.proposal_id,
                    args.decision,
                    reviewer=args.reviewer,
                    expected_row_version=args.expected_row_version,
                )
            )
        return 0

    if args.command == "eval":
        service = EvaluationService(args.workspace)
        if args.eval_command == "run":
            if args.mode == "isolated":
                if args.baseline_root is None or args.candidate_root is None:
                    raise SystemExit("isolated evaluation requires --baseline-root and --candidate-root")
                suite = isolated_skill_suite()
                service = EvaluationService(
                    args.workspace,
                    runner=FileBackedIsolatedRunner(
                        args.baseline_root,
                        args.candidate_root,
                        allowed_root=args.baseline_root.parent,
                    ),
                )
                if args.suite not in {suite.suite_id, "m5-isolated-skill", "m4-local-fixture-v2"}:
                    raise SystemExit("unknown isolated evaluation suite")
            else:
                suite = local_fixture_suite()
                if args.suite not in {suite.suite_id, "local", "m4-local-fixture"}:
                    raise SystemExit("unknown local evaluation suite")
            _print(service.run(args.proposal_id, suite=suite, mode=args.mode))
        elif args.eval_command == "report":
            _print(service.report(args.eval_id))
        return 0

    if args.command == "release":
        service = ReleasePlanService(args.workspace)
        if args.release_command == "materialize":
            proposal = service.proposals.get(args.proposal_id)
            if proposal is None:
                raise SystemExit("proposal does not exist")
            _print(
                FileArtifactService(args.workspace).materialize(
                    proposal,
                    args.baseline_root,
                    args.candidate_root,
                    isolated_root=args.isolated_root,
                )
            )
        elif args.release_command == "eligible":
            manifest = _load_manifest(args.workspace, args.proposal_id, args.candidate_manifest)
            approval = None
            if args.approval_ref:
                approval = json.loads(args.approval_ref.read_text(encoding="utf-8"))
            _print(
                service.eligibility.assess(
                    args.proposal_id,
                    args.evaluation_run_id,
                    manifest,
                    args.target or manifest.target_root,
                    args.target_scope,
                    approval,
                )
            )
        elif args.release_command == "plan":
            manifest = _load_manifest(args.workspace, args.proposal_id, args.candidate_manifest)
            _print(
                service.create_plan(
                    args.proposal_id,
                    args.evaluation_run_id,
                    manifest,
                    args.isolated_root,
                    target_skill_name=args.skill_name or None,
                    target_scope=args.target_scope,
                )
            )
        elif args.release_command == "approve":
            _print(service.approve(args.plan_id, reviewer=args.reviewer, expected_plan_hash=args.expected_hash))
        elif args.release_command == "verify":
            _print(service.verify(args.plan_id, isolated_root=args.isolated_root))
        elif args.release_command == "execute":
            _print(service.execute(args.plan_id, isolated_root=args.isolated_root))
        elif args.release_command == "status":
            _print(service.status(args.plan_id))
        elif args.release_command == "observe":
            _print(
                ReleaseObservationService(args.workspace).observe(
                    args.release_id,
                    ObservationWindow(
                        before_start=args.before_start,
                        before_end=args.before_end,
                        after_start=args.after_start,
                        after_end=args.after_end,
                        min_samples=args.min_samples,
                        task_category=getattr(args, "task_category", ""),
                        source=getattr(args, "source", ""),
                        environment_fingerprint=getattr(args, "environment", ""),
                        agent_id=getattr(args, "agent_id", ""),
                        runner_id=getattr(args, "runner_id", ""),
                        comparison_manifest_id=getattr(args, "manifest_id", ""),
                        source_evidence_tier=getattr(args, "source_tier", ""),
                        experiment_id=getattr(args, "experiment_id", ""),
                        unknown_max_ratio=getattr(args, "unknown_max_ratio", 0.25),
                    ),
                    comparison_manifest=(
                        json.loads(args.manifest.read_text(encoding="utf-8"))
                        if getattr(args, "manifest", None)
                        else None
                    ),
                )
            )
        elif args.release_command == "rollback":
            _print(service.rollback(args.release_id, args.version))
        elif args.release_command == "freeze-manifest":
            try:
                value = json.loads(args.manifest.read_text(encoding="utf-8"))
                manifest = FrozenComparisonManifest.from_dict(value)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise SystemExit(f"invalid comparison manifest: {exc}") from exc
            _print(ReleaseObservationService(args.workspace).freeze_manifest(args.release_id, manifest))
        return 0

    if args.command == "agent":
        if args.agent_command == "codex":
            if bool(args.input) == bool(args.input_file):
                raise SystemExit("provide exactly one of --input or --input-file")
            try:
                raw_input = args.input_file.read_text(encoding="utf-8") if args.input_file else args.input
                task_input = json.loads(raw_input)
            except (OSError, json.JSONDecodeError) as exc:
                raise SystemExit("task input must be valid UTF-8 JSON") from exc
            evidence = CodexRealTaskRunner(
                args.workspace,
                codex_home=args.codex_home,
                cwd=args.cwd,
                model_id=args.model,
                timeout=args.timeout,
                extra_args=args.extra_arg,
            ).run(
                case_id=args.case_id,
                task_input=task_input,
                baseline_or_candidate=args.side,
                artifact_hash=args.artifact_hash,
                capability_version_id=args.capability_version_id,
                pair_id=args.pair_id,
                experiment_id=args.experiment_id,
                comparison_manifest_hash=args.comparison_manifest_hash,
                task_category=args.task_category,
                agent_id=args.agent_id,
                provider_model=args.model,
            )
            _print(evidence)
            return 0 if evidence.execution_status == "completed" else 2

    if args.command == "feedback":
        feedback_service = OptimizationFeedbackService(args.workspace)
        if args.feedback_command == "record":
            observation = ReleaseObservationService(args.workspace).get(args.observation)
            if observation is None:
                raise SystemExit("observation does not exist")
            _print(
                feedback_service.record(
                    observation,
                    issue_id=args.issue,
                    diagnosis_id=args.diagnosis,
                    proposal_id=args.proposal,
                    evaluation_id=args.evaluation,
                    experiment_id=args.experiment_id,
                    reviewed_at=args.reviewed_at,
                )
            )
        elif args.feedback_command == "list":
            _print(feedback_service.list(release_id=args.release_id, observation_id=args.observation_id, limit=args.limit))
        elif args.feedback_command == "show":
            value = feedback_service.get(args.feedback_id)
            if value is None:
                raise SystemExit("feedback does not exist")
            _print(value)
        return 0

    ingestor = TraceIngestor(args.workspace)
    if args.trace_command == "import":
        _print(ingestor.import_jsonl(args.jsonl))
    elif args.trace_command == "show":
        store = ingestor.traces
        _print({"trace": store.get(args.trace_id), "events": store.list_events(args.trace_id)})
    elif args.trace_command == "events":
        _print(
            ingestor.traces.list_events(
                args.trace_id,
                adapter_id=args.adapter_id,
                capability_id=args.capability_id,
                error_type=args.error_type,
            )
        )
    elif args.trace_command == "prune":
        _print({"deleted_payloads": ingestor.traces.delete_payloads(args.before, trace_id=args.trace_id)})
        return 0

    return 0
