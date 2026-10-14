"""Campus Customs backend API (Homework 5, Problem 7).

Start from the backend/ folder with:   uvicorn main:app --reload --port 8000
(see output/harness.md, "Problem 7", about the literal `uvicorn main` form.)

LOCAL DEVELOPMENT API. It is not a production financial-authorization system: one shared
admin token, no user accounts, no rate limiting, no CORS, no TLS.

Routes
    GET  /tickets                 the three tickets: workflow state (open/resolved) and derived business state
    POST /tickets/{id}/run        run the multi-agent team on one ticket (recommend only). Needs the admin token.
                                  ?async=true returns 202 + run_id at once and runs in the background (the dashboard)
    GET  /runs/{run_id}           status and result of an asynchronous run
    GET  /tickets/{id}/decision   the latest recorded run summary for a ticket
    GET  /events                  recent agent events and MCP tool activity (audit trail); ?after=<audit_id> cursor
    GET  /payments/preview        server-verified details of one payable, shown before a human approves
    POST /payments/approve        human-approved payment. CHANGES CASH. Needs the admin token
    GET  /cash                    current checking balance
    POST /database/reset          restore the working database from the original. Needs the admin token

Agents never reach the two write routes: their MCP toolset is the read-only server only.
Every path comes from this file's location, so the working directory does not matter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents import build_mcp_toolset, build_team, load_env
from audit import AuditCorruptError, AuditTrail, new_id, redact, shorten, utc_now
from mcp_clients import call_ledger_tool, call_read_tool
from models import (
    AuditRecord,
    CashResponse,
    EventsResponse,
    PaymentApprovalRequest,
    PaymentApprovalResponse,
    PaymentPreview,
    ResetRequest,
    ResetResponse,
    RunLimits,
    RunResponse,
    RunStartResponse,
    RunStatusResponse,
    TicketDecision,
    TicketDecisionResponse,
    TicketsResponse,
    UsageSummary,
)
from orchestrator import process_ticket_detailed

log = logging.getLogger("campus_customs")  # diagnostics go here (server side), never into API responses

ROOT = Path(__file__).resolve().parent.parent
ADMIN_TOKEN_ENV = "CAMPUS_ADMIN_TOKEN"
MIN_TOKEN_LENGTH = 16
RUN_TIMEOUT_S = 180
# The only browser origin allowed to call this API (the Vite dev server). Localhost-only hosts are accepted.
ALLOWED_ORIGIN = "http://localhost:5173"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
MAX_RUN_RECORDS = 100
SUMMARY_TEXT_LIMIT = 60_000
TeamFactory = Callable[[Any], dict[str, Any]]


@dataclass
class RunRecord:
    """In-memory record of one asynchronous run (the audit trail is the durable record)."""

    run_id: str
    ticket_id: int
    run_mode: str
    started_at: str
    status: str = "running"
    finished_at: str | None = None
    decision: TicketDecision | None = None
    agent_reports: list[dict[str, Any]] = field(default_factory=list)
    usage: UsageSummary | None = None
    resolved: bool = False
    note: str | None = None


class Gate:
    """Keeps agent runs, payments and resets from overlapping (single process only)."""

    def __init__(self) -> None:
        self.active_runs: set[int] = set()
        self.exclusive: str | None = None  # "payment" | "reset" while one is in progress


def _error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


def _audit_down(exc: AuditCorruptError, status: int = 503, prefix: str = "") -> HTTPException:
    """503 for an unreadable audit trail. The response carries only the fixed sentence; the path goes to the log."""
    log.warning("audit trail unusable: %s", exc.diagnostic)
    return _error(status, "audit_unavailable", f"{prefix}{exc}")


_SAFE_ERROR_TYPES = {"UsageLimitExceeded"}  # benign message (the limit numbers); every other error text stays in the server log


def _public_event(r: dict[str, Any]) -> dict[str, Any]:
    """What /events may show: raw exception text from error-type records is replaced by a fixed sentence."""
    ev = str(r.get("event") or "")
    d = dict(r.get("details") or {})
    if ev == "error" and str(d.get("type") or "") not in _SAFE_ERROR_TYPES:
        d = {"type": "InternalError", "message": "The agent step failed. Details are in the server log."}
    elif ev.endswith("_error") or ev == "ticket_resolve_failed":
        if "error" in d:
            d["error"] = "See the server log."
    return {**r, "details": d}


# --------------------------------------------------------------------------- #
# Authentication for the routes that change data (payments, reset) or spend model tokens (run)
# --------------------------------------------------------------------------- #
def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Bearer token from the CAMPUS_ADMIN_TOKEN environment variable. Fails closed.

    The token is never logged, returned or placed in an error message.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV, "")
    if len(expected) < MIN_TOKEN_LENGTH:
        raise _error(503, "admin_token_not_configured", f"Set {ADMIN_TOKEN_ENV} (at least {MIN_TOKEN_LENGTH} characters) before using this route.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "detail": "A valid admin token is required."}, headers={"WWW-Authenticate": "Bearer"})
    if not secrets.compare_digest(supplied.strip().encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "detail": "A valid admin token is required."}, headers={"WWW-Authenticate": "Bearer"})


async def _parse(request: Request, model: type) -> Any:
    """Validate the body here (after authentication has already run), so unauthenticated callers learn nothing."""
    try:
        return model.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]} for e in exc.errors()]
        raise HTTPException(status_code=422, detail={"code": "invalid_request", "detail": "The request body is not valid.", "errors": errors}) from None


_LEDGER_STATUS = {
    "invalid_input": 400,
    "not_found": 404,
    "duplicate": 409,
    "not_payable": 409,
    "amount_mismatch": 409,
    "insufficient_funds": 409,
    "busy": 409,
    "forbidden_target": 403,
}


def create_app(
    team_factory: TeamFactory | None = None,
    run_mode: str = "live",
    audit_path: str | os.PathLike[str] | None = None,
    limits: RunLimits | None = None,
    run_timeout_s: float = RUN_TIMEOUT_S,
) -> FastAPI:
    """Build the app. Tests pass a stub ``team_factory``; production uses the defaults (gpt-6-luna)."""
    load_env()  # CAMPUS_ADMIN_TOKEN / PORTKEY_API_KEY may live in <repository>/.env (see .env.example)
    runs: dict[str, RunRecord] = {}
    tasks: set[asyncio.Task[Any]] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Shutdown: cancel background runs and wait for them, so MCP child processes are closed.
        for task in list(tasks):
            task.cancel()
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)

    app = FastAPI(title="Campus Customs API", version="8.0", lifespan=lifespan, description="Local development API. Not production-ready financial authorization.")
    # Only localhost Host headers (blocks DNS-rebinding pages) and only the Vite origin for CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ALLOWED_ORIGIN],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    audit = AuditTrail(audit_path)
    gate = Gate()
    run_limits = limits or RunLimits()
    factory: TeamFactory = team_factory or (lambda toolset: build_team(toolset, limits=run_limits))
    app.state.gate, app.state.audit, app.state.runs, app.state.tasks = gate, audit, runs, tasks

    # ---------------------------------------------------------------- tickets
    @app.get("/tickets", response_model=TicketsResponse, summary="List tickets and whether each is open or resolved")
    async def list_tickets() -> TicketsResponse:
        data = await call_read_tool("list_all_tickets")
        return TicketsResponse(
            business_date=data.get("business_date"),
            count=data["count"],
            tickets=[
                {
                    "ticket_id": t["ticket_id"], "type": t["type"], "subject": t["subject"], "sku": t["sku"],
                    "size": t["size"], "qty": t["qty"], "lease_id": t["lease_id"], "invoice_id": t["invoice_id"],
                    "state": t["state"], "raw_status": t["raw_status"], "created_at": t["created_at"],
                    "business": t.get("business"),
                }
                for t in data["tickets"]
            ],
        )

    @app.post(
        "/tickets/{ticket_id}/run",
        response_model=None,
        responses={200: {"model": RunResponse}, 202: {"model": RunStartResponse}},
        summary="Run the multi-agent team on one ticket (recommendation only). Requires the admin token. ?async=true returns 202 immediately.",
    )
    async def run_ticket(ticket_id: int, background: bool = Query(False, alias="async"), _auth: None = Depends(require_admin)) -> Any:
        # Authentication runs first (a dependency), so nothing below happens for an unauthorized caller:
        # no MCP call, no agent construction, no model call, no audit write.
        listing = await call_read_tool("list_all_tickets")
        ticket = next((t for t in listing["tickets"] if t["ticket_id"] == ticket_id), None)
        if ticket is None:
            raise _error(404, "ticket_not_found", f"No ticket {ticket_id}.")
        if ticket["state"] != "open":
            raise _error(409, "ticket_not_open", f"Ticket {ticket_id} is not open.")
        if gate.exclusive:
            raise _error(409, "busy", f"A {gate.exclusive} is in progress. Try again when it finishes.")
        if ticket_id in gate.active_runs:
            raise _error(409, "already_running", f"Ticket {ticket_id} is already being processed.")
        if background:
            run_id = new_id()
            record = RunRecord(run_id=run_id, ticket_id=ticket_id, run_mode=run_mode, started_at=utc_now())
            runs[run_id] = record
            for old in [k for k, r in runs.items() if r.status != "running"][: max(0, len(runs) - MAX_RUN_RECORDS)]:
                runs.pop(old, None)
            gate.active_runs.add(ticket_id)  # held until the background task ends (see _execute)
            task = asyncio.create_task(_execute(record), name=f"run-{run_id}")
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            return JSONResponse(status_code=202, content=RunStartResponse(run_id=run_id, ticket_id=ticket_id, status="running", run_mode=run_mode).model_dump())  # type: ignore[arg-type]
        gate.active_runs.add(ticket_id)
        try:
            toolset = build_mcp_toolset()
            try:
                team = factory(toolset)
            except RuntimeError as exc:  # e.g. the model API key is not configured
                raise _error(503, "model_not_configured", "The agent model is not configured on the server.") from exc
            async with toolset:
                decision, run_id, usage = await asyncio.wait_for(
                    process_ticket_detailed(ticket_id, team, audit, run_limits, run_mode),  # type: ignore[arg-type]
                    timeout=run_timeout_s,
                )
        except AuditCorruptError as exc:
            raise _audit_down(exc) from exc
        except asyncio.TimeoutError as exc:
            raise _error(504, "run_timeout", f"The run exceeded {run_timeout_s:g} seconds and was cancelled.") from exc
        finally:
            gate.active_runs.discard(ticket_id)
        return RunResponse(
            run_id=run_id, ticket_id=ticket_id, run_mode=run_mode,  # type: ignore[arg-type]
            decision=decision,
            usage=UsageSummary(requests=usage.requests, tool_calls=usage.tool_calls, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens),
        )

    # ----------------------------------------------------------------- events
    @app.get("/events", response_model=EventsResponse, summary="Recent agent events and MCP tool activity")
    async def events(
        limit: int = Query(50, ge=1, le=200),
        ticket_id: int | None = None,
        run_id: str | None = None,
        agent: str | None = None,
        event: str | None = None,
        run_mode: str | None = Query(None, pattern="^(live|stub)$"),
        after: str | None = Query(None, min_length=1, max_length=64, description="audit_id cursor: return records written after this one, oldest first"),
    ) -> EventsResponse:
        try:
            records = audit.read()
        except AuditCorruptError as exc:
            raise _audit_down(exc) from exc
        if after is not None:
            idx = next((i for i, r in enumerate(records) if r.get("audit_id") == after), None)
            if idx is None:
                raise _error(404, "cursor_not_found", "No audit record has that audit_id.")
            records = records[idx + 1 :]

        def is_stub(r: dict[str, Any]) -> bool:  # records written before run_mode existed are stub runs
            return r.get("run_mode") == "stub" or str(r.get("model") or "").startswith("stub-")

        stub_run_ids = {r["run_id"] for r in records if is_stub(r)}

        def keep(r: dict[str, Any]) -> bool:
            if ticket_id is not None and r.get("ticket_id") != ticket_id:
                return False
            if run_id and r.get("run_id") != run_id:
                return False
            if agent and r.get("agent") != agent:
                return False
            if event and r.get("event") != event:
                return False
            if run_mode and ((r.get("run_id") in stub_run_ids) != (run_mode == "stub")):
                return False
            return True

        matching = [r for r in records if keep(r)]
        shown = [_public_event(r) for r in (matching[:limit] if after is not None else matching[-limit:])]
        if event != "run_summary":  # the full summary is large; GET /tickets/{id}/decision serves it
            shown = [
                {**r, "details": {"note": "Full run summary is served by GET /tickets/{id}/decision.", "outcome": r["details"].get("outcome")}}
                if r.get("event") == "run_summary" else r
                for r in shown
            ]
        return EventsResponse(total_matching=len(matching), returned=len(shown), events=shown)

    # ------------------------------------------------- asynchronous runs (Problem 8)
    async def _resolve(rec: RunRecord) -> None:
        """After a NORMAL finish only: mark the ticket's workflow resolved. Never pays or approves anything."""
        details = {"ticket_id": rec.ticket_id, "note": "Agent workflow finished. The business issue is not resolved by this."}
        try:
            res = await call_ledger_tool("mark_ticket_resolved", {"ticket_id": rec.ticket_id})
        except Exception as exc:  # noqa: BLE001
            log.error("could not mark ticket %s resolved: %s: %s", rec.ticket_id, type(exc).__name__, redact(str(exc))[:500])
            rec.note = "The run finished, but the ticket could not be marked resolved (ledger service error)."
            _try_system(audit, rec, "ticket_resolve_failed", {**details, "error": type(exc).__name__})
            return
        if not res.get("ok"):
            rec.note = f"The run finished, but the ticket was not marked resolved: {res.get('message', 'rejected')}"
            _try_system(audit, rec, "ticket_resolve_failed", {**details, "code": res.get("code"), "message": res.get("message")})
            return
        rec.resolved = True
        if not _try_system(audit, rec, "ticket_resolved", {**details, "status_before": res.get("status_before"), "status_after": res.get("status_after"), "changed": res.get("changed")}):
            rec.note = "The ticket was marked resolved, but that outcome could not be added to the audit trail."

    async def _execute(rec: RunRecord) -> None:
        """Background run. Every exit path records an outcome, releases the gate, and closes MCP processes."""
        ticket_id, reports = rec.ticket_id, []
        try:
            toolset = build_mcp_toolset()
            try:
                team = factory(toolset)
            except RuntimeError:  # e.g. the model API key is not configured
                rec.status, rec.note = "failed", "The agent model is not configured on the server."
                return
            async with toolset:  # closes the MCP child process even when the run is cancelled
                decision, _run_id, usage = await asyncio.wait_for(
                    process_ticket_detailed(ticket_id, team, audit, run_limits, run_mode, run_id=rec.run_id, reports_sink=reports),  # type: ignore[arg-type]
                    timeout=run_timeout_s,
                )
            rec.decision, rec.agent_reports = decision, list(reports)
            rec.usage = UsageSummary(requests=usage.requests, tool_calls=usage.tool_calls, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
            outcome = "blocked" if decision.status == "blocked" else "finished"
            try:
                _system_write(audit, rec.run_id, ticket_id, run_mode, "run_summary", {
                    "outcome": outcome,
                    "decision_json": decision.model_dump_json(),
                    "reports_json": json.dumps(rec.agent_reports),
                    "usage_json": rec.usage.model_dump_json(),
                }, SUMMARY_TEXT_LIMIT)
            except AuditCorruptError as exc:
                log.warning("audit trail unusable: %s", exc.diagnostic)
                rec.status, rec.note = "failed", "The audit trail cannot be written, so the run was not recorded and the ticket was not resolved. " + str(exc)
                return
            if outcome == "blocked":  # stopped by a limit or an error: NOT resolved
                rec.status, rec.note = "failed", decision.summary
                return
            await _resolve(rec)  # status stays "running" until this finishes, so pollers never see a half-done run
            rec.status = "finished"
        except asyncio.TimeoutError:
            rec.status, rec.note = "failed", f"The run exceeded {run_timeout_s:g} seconds and was cancelled. The ticket was not resolved."
        except AuditCorruptError as exc:
            log.warning("audit trail unusable: %s", exc.diagnostic)
            rec.status, rec.note = "failed", "The audit trail cannot be written, so the ticket was not resolved. " + str(exc)
        except asyncio.CancelledError:
            rec.status, rec.note = "failed", "The run was cancelled because the server is shutting down."
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("background run %s failed: %s: %s", rec.run_id, type(exc).__name__, redact(str(exc))[:500])
            rec.status, rec.note = "failed", "The run failed unexpectedly. The ticket was not resolved. A person should check the server log."
        finally:
            rec.finished_at = utc_now()
            if rec.status == "running":
                rec.status = "failed"
            gate.active_runs.discard(ticket_id)

    @app.get("/runs/{run_id}", response_model=RunStatusResponse, summary="Status and result of an asynchronous run")
    async def get_run(run_id: str) -> RunStatusResponse:
        rec = runs.get(run_id)
        if rec is None:
            raise _error(404, "run_not_found", "No such run (unknown id, or the server restarted).")
        return RunStatusResponse(
            run_id=rec.run_id, ticket_id=rec.ticket_id, status=rec.status, run_mode=rec.run_mode,  # type: ignore[arg-type]
            started_at=rec.started_at, finished_at=rec.finished_at, decision=rec.decision,
            agent_reports=rec.agent_reports, usage=rec.usage, resolved=rec.resolved, note=rec.note,
        )

    @app.get("/tickets/{ticket_id}/decision", response_model=TicketDecisionResponse, summary="Latest recorded run summary for a ticket")
    async def ticket_decision(ticket_id: int) -> TicketDecisionResponse:
        try:
            records = audit.read()
        except AuditCorruptError as exc:
            raise _audit_down(exc) from exc
        rec = next((r for r in reversed(records) if r.get("event") == "run_summary" and r.get("ticket_id") == ticket_id), None)
        if rec is None:
            raise _error(404, "no_run_summary", f"No run has been recorded for ticket {ticket_id}.")
        try:
            d = rec["details"]
            return TicketDecisionResponse(
                ticket_id=ticket_id, run_id=rec["run_id"], outcome=d["outcome"], finished_at=rec["timestamp_utc"],
                decision=json.loads(d["decision_json"]), agent_reports=json.loads(d["reports_json"]),
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise _error(500, "summary_unreadable", "The recorded run summary could not be read (it may have been too large to store).") from exc

    # ------------------------------------------------------- payment preview
    @app.get("/payments/preview", response_model=PaymentPreview, summary="Server-verified details of one payable (read-only; does not approve anything)")
    async def payment_preview(
        target_type: Literal["invoice", "rent"],
        target_id: int = Query(ge=1, le=1_000_000),
        ticket_id: int | None = Query(None, ge=1, le=1_000_000),
    ) -> PaymentPreview:
        cash_data = await call_read_tool("get_cash_position")
        checking = next((a for a in cash_data["accounts"] if a["name"] == "checking"), None)
        if checking is None:
            raise _error(404, "account_not_found", "There is no checking account.")
        balance = round(float(checking["balance"]), 2)
        caveats: list[str] = []
        if target_type == "invoice":
            data = await call_read_tool("get_invoice_status", {"invoice_id": target_id})
            if not data.get("found"):
                raise _error(404, "not_found", f"No invoice {target_id}.")
            inv = data["invoices"][0]
            amount, payee, description = float(inv["amount"]), inv["vendor_name"] or "Unknown vendor", inv["description"]
            due_date = inv["due_date"]
            days = -inv["days_past_due"] if inv["days_past_due"] is not None else None
            existing = inv["recorded_payments"][0]["payment_id"] if inv["recorded_payments"] else None
            already_paid = str(inv["status"]).lower() == "paid" or existing is not None
            billing = None
            if ticket_id is not None:
                ctx = await call_read_tool("get_ticket_context", {"ticket_id": ticket_id})
                short = ((ctx.get("linked") or {}).get("requested_size") or {}) if ctx.get("found") and ctx["ticket"].get("invoice_id") == target_id else {}
                if short.get("shortfall"):
                    vend = await call_read_tool("get_vendor_info", {"vendor_id": inv["vendor_id"]})
                    lead = vend["vendors"][0]["lead_days"] if vend.get("found") else None
                    caveats.append(
                        "Paying this invoice does not guarantee the item can ship. The customer's size currently has "
                        f"{short.get('qty_on_hand')} on hand" + (f" and the vendor's reprint lead time is {lead} days." if lead is not None else ".")
                    )
        else:
            data = await call_read_tool("get_rent_obligation", {"lease_id": target_id})
            if not data.get("found"):
                raise _error(404, "not_found", f"No lease {target_id}.")
            lease = data["leases"][0]
            amount, payee, description = float(lease["monthly_rent"]), lease["landlord"], lease["space_name"]
            due_date, days = lease["next_due"], lease["days_until_due"]
            pp = lease["period_payment"]
            billing, existing, already_paid = pp["billing_period"], pp["payment_id"], bool(pp["payment_recorded"])
        blockers: list[dict[str, str]] = []
        if already_paid:
            blockers.append({"code": "already_paid", "message": "A payment is already recorded for this obligation."})
        if round(balance - amount, 2) < 0:
            blockers.append({"code": "insufficient_funds", "message": "The checking balance does not cover this payment."})
        return PaymentPreview(
            target_type=target_type, target_id=target_id, payee=payee, description=description, amount=amount,
            amount_text=f"{amount:.2f}", due_date=due_date, days_until_due=days, billing_period=billing,
            already_paid=already_paid, existing_payment_id=existing, cash_before=balance,
            cash_after=round(balance - amount, 2), can_approve=not blockers, blockers=blockers,  # type: ignore[arg-type]
            caveats=caveats, business_date=cash_data.get("business_date"),
        )

    # ------------------------------------------------------------------- cash
    @app.get("/cash", response_model=CashResponse, summary="Current checking balance")
    async def cash() -> CashResponse:
        data = await call_read_tool("get_cash_position")
        checking = next((a for a in data["accounts"] if a["name"] == "checking"), None)
        if checking is None:
            raise _error(404, "account_not_found", "There is no checking account.")
        return CashResponse(account="checking", balance=checking["balance"], as_of=checking["as_of"])

    # --------------------------------------------------------------- payments
    async def _human_action(
        kind: str, details: dict[str, Any], operation: Callable[[], Awaitable[dict[str, Any]]]
    ) -> tuple[str, dict[str, Any], bool]:
        """Exclusive section: attempt record -> operation -> outcome record.

        Returns (approval_id, ledger_result, outcome_audit_ok). A failed attempt record stops everything
        before any change is made.
        """
        if gate.exclusive or gate.active_runs:
            busy = gate.exclusive or f"ticket run ({sorted(gate.active_runs)})"
            raise _error(409, "busy", f"A {busy} is in progress. Try again when it finishes.")
        gate.exclusive = kind
        approval_id = new_id()
        try:
            try:
                _write(audit, approval_id, f"{kind}_attempt", details)
            except AuditCorruptError as exc:
                raise _audit_down(exc, prefix="Nothing was changed, because the audit trail cannot be written. ") from exc
            try:
                result = await operation()
            except Exception as exc:  # noqa: BLE001
                log.error("ledger service failed during %s: %s: %s", kind, type(exc).__name__, redact(str(exc))[:500])
                outcome_ok = _try_write(audit, approval_id, f"{kind}_error", {**details, "error": f"{type(exc).__name__}: {exc}"})
                raise _error(502, "ledger_error", f"The ledger service failed, so the outcome is unknown. Check GET /cash and GET /events. Audit outcome recorded: {outcome_ok}.") from exc
            return approval_id, result, True
        finally:
            gate.exclusive = None

    @app.post("/payments/approve", response_model=PaymentApprovalResponse, summary="Approve ONE payment (invoice or rent). Changes cash. Requires the admin token.")
    async def approve_payment(request: Request, _auth: None = Depends(require_admin)) -> PaymentApprovalResponse:
        body: PaymentApprovalRequest = await _parse(request, PaymentApprovalRequest)
        details = {"target_type": body.target_type, "target_id": body.target_id, "expected_amount": str(body.expected_amount), "approved_by": body.approved_by}

        async def op() -> dict[str, Any]:
            return await call_ledger_tool("record_approved_payment", {"target_type": body.target_type, "target_id": body.target_id, "expected_amount": str(body.expected_amount), "approved_by": body.approved_by})

        approval_id, result, _ = await _human_action("payment", details, op)
        if not result.get("ok"):
            outcome = _try_write(audit, approval_id, "payment_rejected", {**details, "code": result.get("code"), "message": result.get("message")})
            raise HTTPException(
                status_code=_LEDGER_STATUS.get(result.get("code", ""), 400),
                detail={"code": result.get("code", "rejected"), "detail": result.get("message", "Rejected."), "nothing_changed": True, "audit_outcome_recorded": outcome},
            )
        try:
            _write(audit, approval_id, "payment_committed", {**details, **{k: result[k] for k in ("payment_id", "kind", "ref_id", "billing_period", "amount", "paid_at", "balance_before", "balance_after", "invoice_status")}})
        except AuditCorruptError as exc:
            log.warning("audit trail unusable: %s", exc.diagnostic)
            raise _error(500, "audit_outcome_failed", f"The payment WAS committed (payment_id {result['payment_id']}, balance now {result['balance_after']}), but its outcome could not be added to the audit trail: {exc}. The earlier attempt record is in the trail.") from exc
        return PaymentApprovalResponse(approval_id=approval_id, **{k: result[k] for k in PaymentApprovalResponse.model_fields if k != "approval_id"})

    # ------------------------------------------------------------------ reset
    @app.post("/database/reset", response_model=ResetResponse, summary="Restore the working database from the original backup. Requires the admin token.")
    async def reset_database(request: Request, _auth: None = Depends(require_admin)) -> ResetResponse:
        await _parse(request, ResetRequest)

        async def op() -> dict[str, Any]:
            return await call_ledger_tool("reset_working_database", {})

        approval_id, result, _ = await _human_action("reset", {"action": "reset working database from the original"}, op)
        if not result.get("ok"):
            outcome = _try_write(audit, approval_id, "reset_rejected", {"code": result.get("code"), "message": result.get("message")})
            raise HTTPException(status_code=_LEDGER_STATUS.get(result.get("code", ""), 409), detail={"code": result.get("code", "rejected"), "detail": result.get("message", "Rejected."), "nothing_changed": True, "audit_outcome_recorded": outcome})
        try:
            _write(audit, approval_id, "reset_committed", {k: result[k] for k in ("original_sha256_before", "original_sha256_after", "working_sha256")})
        except AuditCorruptError as exc:
            log.warning("audit trail unusable: %s", exc.diagnostic)
            raise _error(500, "audit_outcome_failed", f"The working database WAS reset, but the outcome could not be added to the audit trail: {exc}.") from exc
        return ResetResponse(approval_id=approval_id, **{k: result[k] for k in ("restored", "original_sha256_before", "original_sha256_after", "working_sha256")})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "detail": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=detail, headers=getattr(exc, "headers", None))

    return app


# --------------------------------------------------------------------------- #
# Audit helpers for human-authorized actions
# --------------------------------------------------------------------------- #
def _write(audit: AuditTrail, approval_id: str, event: str, details: dict[str, Any]) -> None:
    from audit import shorten, utc_now

    audit.append(
        AuditRecord(
            audit_id=new_id(), timestamp_utc=utc_now(), run_id=f"human-{approval_id}", ticket_id=None,
            step=1 if event.endswith("_attempt") else 2, agent="human", parent_agent=None, depth=0, event=event,  # type: ignore[arg-type]
            details={k: shorten(v, 400) for k, v in details.items()}, usage={}, model=None, run_mode=None,
        )
    )


def _system_write(audit: AuditTrail, run_id: str, ticket_id: int, run_mode: str, event: str, details: dict[str, Any], limit: int = 400) -> None:
    """Audit records written by the API itself around an asynchronous run (agent "system")."""
    audit.append(
        AuditRecord(
            audit_id=new_id(), timestamp_utc=utc_now(), run_id=run_id, ticket_id=ticket_id, step=10_000,
            agent="system", parent_agent=None, depth=0, event=event,  # type: ignore[arg-type]
            details={k: shorten(v, limit) for k, v in details.items()}, usage={}, model=None, run_mode=run_mode,  # type: ignore[arg-type]
        )
    )


def _try_system(audit: AuditTrail, rec: Any, event: str, details: dict[str, Any]) -> bool:
    try:
        _system_write(audit, rec.run_id, rec.ticket_id, rec.run_mode, event, details)
        return True
    except AuditCorruptError:
        return False


def _try_write(audit: AuditTrail, approval_id: str, event: str, details: dict[str, Any]) -> bool:
    try:
        _write(audit, approval_id, event, details)
        return True
    except AuditCorruptError:
        return False


app = create_app()
