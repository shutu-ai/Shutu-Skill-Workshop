"""Run the bounded three-case M6.1 Codex pair in isolated artifacts only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shutu_workshop import CodexRealTaskRunner, TraceEvent
from shutu_workshop.release import artifact_tree_hash


def oracle(events: list[TraceEvent]) -> dict[str, object]:
    """Independent of task input: judge only observed MCP result events."""

    results = [event for event in events if event.type in {"mcp.tool_result", "tool.result"}]
    successful = [event for event in results if event.status in {"ok", "success"}]
    return {
        "oracle_id": "m61.observed-tool-result",
        "oracle_version": "1",
        "verdict": "pass" if successful else "unknown",
        "checked_event_refs": [event.event_id for event in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()
    baseline = args.baseline_root.resolve()
    candidate = args.candidate_root.resolve()
    cases = [
        {"case_id": "target", "task_input": {"instruction": "Call the echo tool.", "text": "target"}},
        {"case_id": "normal-regression", "task_input": {"instruction": "Call the echo tool.", "text": "normal"}},
        {"case_id": "holdout-boundary", "task_input": {"instruction": "Call the echo tool.", "text": "holdout"}},
    ]
    for case in cases:
        case["baseline_artifact_root"] = str(baseline)
        case["candidate_artifact_root"] = str(candidate)
        case["baseline_artifact_hash"] = artifact_tree_hash(baseline)
        case["candidate_artifact_hash"] = artifact_tree_hash(candidate)
        case["task_input_hash"] = "not-persisted"
    runner = CodexRealTaskRunner(args.workspace, codex_home=args.codex_home, timeout=args.timeout)
    results = runner.run_pair(
        cases,
        pair_id="m61-real-pair",
        experiment_id="m61-real-experiment",
        task_category="m61-real-agent",
        oracle=oracle,
    )
    payload = {
        "experiment_id": "m61-real-experiment",
        "pair_id": "m61-real-pair",
        "case_count": len(cases),
        "results": [result.to_dict() for result in results],
        "raw_task_inputs_persisted": False,
        "credentials_persisted": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case_count": len(cases), "output": str(args.output), "statuses": [result.execution_status for result in results]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
