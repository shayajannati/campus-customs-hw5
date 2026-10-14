"""MCP wrapper for the restricted ledger (Homework 5, Problem 7).

This is a SEPARATE MCP server from the read-only agent-facing ``server.py``. The agent team's
toolset is only ever pointed at ``server.py``; only the FastAPI app's payment and reset routes
(after admin-token authentication) connect here. Never register these tools with an agent.

Run (stdio):   python mcp_server/ledger_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

mcp = FastMCP("campus-customs-ledger")


def _result(fn, *args) -> dict:
    """Business refusals come back as data ({ok: false, code, message}) so callers can tell them from crashes."""
    try:
        return {"ok": True, **fn(*args)}
    except ledger.Rejected as exc:
        return {"ok": False, "code": exc.code, "message": exc.message, **exc.extra}


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False})
def record_approved_payment(target_type: str, target_id: int, expected_amount: str, approved_by: str) -> dict:
    """Record ONE human-approved payment from checking: an invoice, or one month of rent.

    Only the authenticated FastAPI payment route may call this. The amount comes from the
    database; `expected_amount` must match it. Rejects duplicates and insufficient funds.
    """
    return _result(ledger.record_payment, target_type, target_id, expected_amount, approved_by)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
def reset_working_database() -> dict:
    """Restore the working database from the untouched original. Only the authenticated reset route may call this."""
    return _result(ledger.reset_working_database)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
def mark_ticket_resolved(ticket_id: int) -> dict:
    """Mark a ticket's agent WORKFLOW as finished (open -> resolved, one way, idempotent).

    This does not pay anything and does not resolve the business issue. Only the authenticated
    ticket-run route calls it, after a run finishes normally.
    """
    return _result(ledger.mark_ticket_resolved, ticket_id)


if __name__ == "__main__":
    mcp.run(show_banner=False)
