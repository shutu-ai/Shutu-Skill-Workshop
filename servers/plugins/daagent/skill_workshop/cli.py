"""Command-line entry point for the local Skill Workshop replica."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .engine import SkillWorkshop


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _print(value: Any) -> None:
    print(json.dumps(_json_value(value), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local DAAgent Skill Workshop replica")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace containing the skills/ directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="List local skills")

    snapshot = commands.add_parser("snapshot", help="Capture a skill fingerprint")
    snapshot.add_argument("skill_name")

    issue = commands.add_parser("issue", help="Record a skill evolution issue")
    issue.add_argument("skill_name")
    issue.add_argument("--category", required=True)
    issue.add_argument("--severity", choices=("low", "medium", "high"), default="medium")
    issue.add_argument("--title", required=True)
    issue.add_argument("--summary", default="")
    issue.add_argument("--evidence-count", type=int, default=0)

    proposal = commands.add_parser("propose", help="Create a proposal from a JSON patch list")
    proposal.add_argument("skill_name")
    proposal.add_argument("--reason", required=True)
    proposal.add_argument("--patches", type=Path, required=True)
    proposal.add_argument("--risk-level", choices=("low", "medium", "high"), default="low")

    accept = commands.add_parser("accept", help="Accept all patches in a proposal")
    accept.add_argument("proposal_id")

    reject = commands.add_parser("reject", help="Reject all patches in a proposal")
    reject.add_argument("proposal_id")

    test = commands.add_parser("test", help="Test accepted patches in a temporary copy")
    test.add_argument("proposal_id")

    apply = commands.add_parser("apply", help="Apply a tested proposal")
    apply.add_argument("proposal_id")
    apply.add_argument("--as-new-skill")

    proposals = commands.add_parser("proposals", help="List proposals")
    proposals.add_argument("--skill-name")

    issues = commands.add_parser("issues", help="List issues")
    issues.add_argument("--skill-name")

    versions = commands.add_parser("versions", help="List versions")
    versions.add_argument("skill_name")

    rollback = commands.add_parser("rollback", help="Rollback to a recorded version")
    rollback.add_argument("skill_name")
    rollback.add_argument("version_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workshop = SkillWorkshop(args.workspace)

    if args.command == "list":
        _print(workshop.list_skills())
    elif args.command == "snapshot":
        _print(workshop.snapshot_skill(args.skill_name))
    elif args.command == "issue":
        _print(
            workshop.record_issue(
                args.skill_name,
                category=args.category,
                severity=args.severity,
                title=args.title,
                summary=args.summary,
                evidence_count=args.evidence_count,
            )
        )
    elif args.command == "propose":
        patches = json.loads(args.patches.read_text(encoding="utf-8"))
        if not isinstance(patches, list):
            raise SystemExit("--patches must contain a JSON array")
        proposal = workshop.create_proposal(
            args.skill_name,
            reason=args.reason,
            changes=patches,
            risk_level=args.risk_level,
        )
        _print(proposal)
    elif args.command == "accept":
        _print(workshop.accept_all(args.proposal_id))
    elif args.command == "reject":
        _print(workshop.reject_all(args.proposal_id))
    elif args.command == "test":
        _print(workshop.test_proposal(args.proposal_id))
    elif args.command == "apply":
        if args.as_new_skill:
            _print(workshop.apply_proposal_as_new_skill(args.proposal_id, args.as_new_skill))
        else:
            _print(workshop.apply_proposal(args.proposal_id))
    elif args.command == "proposals":
        _print(workshop.list_proposals(args.skill_name))
    elif args.command == "issues":
        _print(workshop.list_issues(args.skill_name))
    elif args.command == "versions":
        _print(workshop.list_versions(args.skill_name))
    elif args.command == "rollback":
        _print(workshop.rollback(args.skill_name, args.version_id))
    return 0
