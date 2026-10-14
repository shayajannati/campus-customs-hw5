"""Run the agent team on open tickets (LIVE: calls gpt-6-luna through Portkey and costs tokens).

Usage:  .venv/bin/python scripts/run_tickets.py 101 102 103
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from orchestrator import process_tickets  # noqa: E402


def main() -> None:
    ids = [int(a) for a in sys.argv[1:]] or [101, 102, 103]
    for decision in asyncio.run(process_tickets(ids)):
        print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
