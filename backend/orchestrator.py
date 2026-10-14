"""Run the agent team on one open ticket."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from agents import build_mcp_toolset, build_team, record, run_logged
from audit import AuditCorruptError, AuditTrail, new_id, redact
from models import RunDeps, RunLimits, RunState, TicketDecision

log = logging.getLogger("campus_customs")
# Shown to users instead of the raw exception text (which can carry paths or provider details).
GENERIC_FAILURE = (
    "The agent run failed unexpectedly. Nothing was paid or changed, and the ticket was not resolved. "
    "A person should check the server log."
)


async def process_ticket_detailed(
    ticket_id: int,
    team: dict[str, Any],
    audit: AuditTrail,
    limits: RunLimits | None = None,
    run_mode: Literal["live", "stub"] = "live",
    run_id: str | None = None,
    reports_sink: list[dict[str, Any]] | None = None,
) -> tuple[TicketDecision, str, RunUsage]:
    """Boss-led run for one ticket. Returns (recommendation, run id, shared usage).

    Nothing is paid, approved or changed. Audit failures propagate (the run stops).
    """
    limits = limits or RunLimits()
    state = RunState(
        run_id=run_id or new_id(), ticket_id=ticket_id, limits=limits, audit=audit, team=team, run_mode=run_mode
    )
    if reports_sink is not None:
        state.reports = reports_sink  # the caller keeps a reference to read each specialist's report
    deps = RunDeps(state=state, chain=("boss",))
    usage = RunUsage()
    prompt = (
        f"Process open ticket {ticket_id}. Read it, delegate to the specialists you need, and return "
        "your final recommendation for the human manager. Recommend only; do not act."
    )
    try:
        result = await run_logged(team["boss"], prompt, deps, usage=usage)
        return result.output, state.run_id, usage
    except AuditCorruptError:
        raise  # fail safe: stop, leave the audit file untouched
    except UsageLimitExceeded as exc:
        return (
            TicketDecision(
                ticket_id=ticket_id,
                status="blocked",
                summary=f"Stopped by a usage limit: {exc}",
                risks=["Run ended early; a human should review the ticket."],
            ),
            state.run_id,
            usage,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("agent run failed for ticket %s: %s: %s", ticket_id, type(exc).__name__, redact(str(exc))[:500])
        return (
            TicketDecision(
                ticket_id=ticket_id,
                status="blocked",
                summary=GENERIC_FAILURE,
                risks=["Run ended early; a human should review the ticket."],
            ),
            state.run_id,
            usage,
        )


async def process_ticket(
    ticket_id: int,
    team: dict[str, Any],
    audit: AuditTrail,
    limits: RunLimits | None = None,
    run_mode: Literal["live", "stub"] = "live",
) -> TicketDecision:
    """Boss-led run for one ticket. Returns a recommendation; nothing is paid, approved or changed."""
    decision, _run_id, _usage = await process_ticket_detailed(ticket_id, team, audit, limits, run_mode)
    return decision


async def process_tickets(ticket_ids: list[int], audit: AuditTrail | None = None, limits: RunLimits | None = None) -> list[TicketDecision]:
    """Connect to the MCP server once, build the five agents (gpt-6-luna), process each ticket."""
    audit = audit or AuditTrail()
    toolset = build_mcp_toolset()
    team = build_team(toolset, limits=limits)
    out: list[TicketDecision] = []
    async with toolset:
        for tid in ticket_ids:
            out.append(await process_ticket(tid, team, audit, limits))
    return out
