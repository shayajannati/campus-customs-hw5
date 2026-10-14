"""TEST-ONLY ASGI entry point: the real Campus Customs API wired to the scripted stub team.

Used only inside a disposable project copy for browser tests:
    uvicorn stub_backend:app --port 8765          (run from the copy's backend/ folder)
Environment: STUB_DELAY (seconds per scripted model step, default 0.6), STUB_FAIL_TICKETS (e.g. "103").
It makes no model calls and uses the copy's own databases and audit trail.
"""

from __future__ import annotations

import os

import main
from stub_team import make_team_factory

_fail = frozenset(int(x) for x in os.environ.get("STUB_FAIL_TICKETS", "").split(",") if x.strip())
app = main.create_app(
    team_factory=make_team_factory(float(os.environ.get("STUB_DELAY", "0.6")), _fail),
    run_mode="stub",
)
