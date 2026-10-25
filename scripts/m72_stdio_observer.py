"""Repository entry point for the opt-in M7.2 transparent MCP observer."""

from __future__ import annotations

import sys
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "servers"))

from shutu_workshop.observers.mcp.stdio import main as observer_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if "--" in values:
        command = values[values.index("--") + 1 :]
        if any("m72_stdio_observer.py" in value.casefold() for value in command):
            print("observer recursion is not allowed", file=sys.stderr)
            return 2
    return observer_main(values)


if __name__ == "__main__":
    raise SystemExit(main())
