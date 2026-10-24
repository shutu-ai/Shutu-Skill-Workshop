"""Small independent M1 CLI; the original SkillWorkshop CLI is unchanged."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .capabilities import CapabilityRegistry
from .ingestion import TraceIngestor
from .models import Capability
from .traces import TraceStore


def _value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_value(item) for item in value]
    return value


def _print(value: Any) -> None:
    print(json.dumps(_value(value), ensure_ascii=False, indent=2))


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
