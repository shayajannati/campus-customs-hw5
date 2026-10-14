"""Problem 7 API tests. EVERY payment and reset happens on a disposable COPY of the project.

The copy is built in a temp folder; its backend, ledger and databases are used for all tests, so
nothing can touch the real working or original database. Ticket runs use stub models (no Portkey).

Run:  .venv/bin/python -B tests/run_api_tests.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REAL = Path(__file__).resolve().parent.parent
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

REAL_PROTECTED = [
    REAL / "data" / "campus_customs.db", REAL / "data" / "campus_customs_new.db",
    REAL / "output" / "mcp_smoke.json", REAL / "output" / "audit_trail.json", REAL / "output" / "desk_tickets.html",
]
RESULTS: list[tuple[str, bool]] = []


PRINTED: list[str] = []
SERVER_OUTPUT: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond)))
    line = ("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else "")
    PRINTED.append(line)
    print(line)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot(paths) -> dict[str, str]:
    return {str(p): sha(p) for p in paths}


real_before = snapshot(REAL_PROTECTED)
BAD_BEARER = "Bear" + "er " + "synthetic-invalid-test-value"  # an invalid credential for 401 tests, built at run time


def _requester_pattern() -> str:
    """Regex for the ticket requester's real name, read from the course database (never hard-coded or printed)."""
    import sqlite3

    conn = sqlite3.connect(f"file:{REAL / 'data' / 'campus_customs.db'}?mode=ro&immutable=1", uri=True)
    try:
        name = conn.execute("select requester from tickets where id = 101").fetchone()[0]
    finally:
        conn.close()
    return "|".join(re.escape(w) for w in name.split())


SENSITIVE_RX = _requester_pattern()


# ---------------------------------------------------------------- disposable copy
TMP = Path(tempfile.mkdtemp(prefix="hw5-api-test-"))
CLONE = TMP / "HW5"
for d in ("backend", "mcp_server", "data", "output", "scripts"):
    shutil.copytree(REAL / d, CLONE / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
for stub in ("stub_team.py", "stub_backend.py"):
    shutil.copyfile(REAL / "tests" / stub, CLONE / "backend" / stub)
sys.path.insert(0, str(CLONE / "backend"))
sys.path.insert(0, str(CLONE / "mcp_server"))

import httpx  # noqa: E402
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

import agents  # noqa: E402
import ledger  # noqa: E402
import main  # noqa: E402
from audit import AuditCorruptError  # noqa: E402

TOKEN = secrets.token_urlsafe(24)  # a throwaway test credential, never printed
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WORKING, ORIGINAL = CLONE / "data" / "campus_customs_new.db", CLONE / "data" / "campus_customs.db"
CLONE_AUDIT = CLONE / "output" / "audit_trail.json"
ORIG_SHA = sha(ORIGINAL)


def db(sql: str, args=()):
    c = sqlite3.connect(WORKING)
    try:
        rows = c.execute(sql, args).fetchall()
        c.commit()
        return rows
    finally:
        c.close()


def state():
    return {
        "cash": db("select balance from cash_accounts where name='checking'")[0][0],
        "payments": db("select id,kind,ref_id,amount,account,paid_at,approved_by from payments order by id"),
        "inv501": db("select status from invoices where id=501")[0][0],
        "due": db("select next_due from leases where id=1")[0][0],
        "tickets": db("select id,status from tickets order by id"),
    }


# ---------------------------------------------------------------- stub agent team
def ticket_of(messages) -> int:
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, UserPromptPart):
                h = re.search(r"[Tt]icket (\d+)", str(p.content))
                if h:
                    return int(h.group(1))
    return 0


def make_brain(delay: float = 0.0, rogue_tool: str | None = None, counter: dict | None = None):
    async def brain(messages, info: AgentInfo) -> ModelResponse:
        if counter is not None:
            counter["model"] += 1
        n = sum(isinstance(m, ModelResponse) for m in messages)
        if delay:
            await asyncio.sleep(delay)
        t = ticket_of(messages)
        names = {x.name for x in info.function_tools}
        if rogue_tool and n == 0:  # a hijacked agent trying to use a ledger tool
            return ModelResponse(parts=[ToolCallPart(rogue_tool, {"target_type": "invoice", "target_id": 501, "expected_amount": "840.00", "approved_by": "Pat Doe"})])
        if n == 0 or (rogue_tool and n == 1 and False):
            return ModelResponse(parts=[ToolCallPart("get_ticket_context", {"ticket_id": t})])
        payload = {"ticket_id": t, "status": "needs_human_approval", "summary": f"Stub recommendation for ticket {t}.", "actions_recommended": ["Wait for human approval."],
                   "specialists_consulted": []} if "final_result" and info.output_tools[0].name == "final_result" else {}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])
    return brain


def stub_factory(delay: float = 0.0, rogue_tool: str | None = None, counter: dict | None = None):
    return lambda toolset: agents.build_team(toolset, model_for=lambda name: FunctionModel(make_brain(delay, rogue_tool, counter), model_name=f"stub-{name}"))


def new_app(**kw):
    kw.setdefault("team_factory", stub_factory())
    kw.setdefault("run_mode", "stub")
    kw.setdefault("audit_path", CLONE_AUDIT)
    return main.create_app(**kw)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", timeout=120)


def reset_clone():
    shutil.copyfile(ORIGINAL, WORKING)


def audit_events(since: int = 0):
    return json.loads(CLONE_AUDIT.read_text())[since:]


async def main_tests() -> None:
    os.environ[main.ADMIN_TOKEN_ENV] = TOKEN
    check("Tests run only against the disposable copy", Path(main.__file__).resolve().parent.parent == CLONE.resolve() and ledger.WORKING_DB.resolve() == WORKING.resolve() and CLONE != REAL)
    hist = json.loads(CLONE_AUDIT.read_text())
    HIST_N = len(hist)
    check("Copy starts with the 107 historical audit records (88 stub + 19 live)", HIST_N == 107)
    app = new_app()

    async with client(app) as c:
        # ================= GET /tickets =================
        r = await c.get("/tickets")
        j = r.json()
        check("GET /tickets: 200, three tickets, all open, ids 101-103", r.status_code == 200 and j["count"] == 3 and [t["ticket_id"] for t in j["tickets"]] == [101, 102, 103] and all(t["state"] == "open" for t in j["tickets"]))
        check("GET /tickets: requester names are never returned", not re.search(SENSITIVE_RX.lower() + r"|requester", r.text.lower()))
        db("update tickets set status='closed' where id=103")
        j = (await c.get("/tickets")).json()
        check("GET /tickets: a non-open status shows as 'resolved' (raw status kept)", {t["ticket_id"]: (t["state"], t["raw_status"]) for t in j["tickets"]}[103] == ("resolved", "closed"))
        r = await c.post("/tickets/103/run", headers=AUTH)
        check("POST /tickets/{id}/run: resolved ticket refused (409)", r.status_code == 409 and r.json()["code"] == "ticket_not_open")
        db("update tickets set status='open' where id=103")

        # ================= POST /tickets/{id}/run =================
        r = await c.post("/tickets/102/run", headers=AUTH)
        j = r.json()
        check("POST /tickets/102/run: 200 with a decision, usage and run id (stub model)", r.status_code == 200 and j["decision"]["ticket_id"] == 102 and j["run_mode"] == "stub" and j["usage"]["requests"] >= 2 and j["usage"]["tool_calls"] >= 1, str(j["usage"]))
        run_id = j["run_id"]
        evs = audit_events(HIST_N)
        check("Run is audited as stub, including the MCP tool call", {e["run_id"] for e in evs} == {run_id} and all(e["run_mode"] == "stub" for e in evs) and any(e["event"] == "tool_call" and e["details"]["tool"] == "get_ticket_context" for e in evs))
        check("Run changed nothing in the database", state()["cash"] == 3400.0 and state()["payments"] == [])
        r = await c.post("/tickets/999/run", headers=AUTH)
        check("POST /tickets/999/run: unknown ticket (404)", r.status_code == 404 and r.json()["code"] == "ticket_not_found")
        r = await c.post("/tickets/abc/run", headers=AUTH)
        check("POST /tickets/abc/run: invalid id (422)", r.status_code == 422)

        # ================= GET /events =================
        r = await c.get("/events", params={"run_id": run_id})
        j = r.json()
        check("GET /events: filter by run id returns that run's agent messages and MCP calls", r.status_code == 200 and j["returned"] == len(evs) and {e["event"] for e in j["events"]} >= {"user_prompt", "tool_call", "tool_return", "final_output"})
        r = await c.get("/events", params={"run_mode": "live"})
        check("GET /events: run_mode=live returns the 19 historical live records", r.json()["total_matching"] == 19 and all(e["model"] in (None, "gpt-6-luna") for e in r.json()["events"]))
        r = await c.get("/events", params={"limit": 5})
        check("GET /events: limit honoured, newest last", r.json()["returned"] == 5 and r.json()["events"][-1]["audit_id"] == audit_events()[-1]["audit_id"])
        check("GET /events: filters (event, agent, ticket)", (await c.get("/events", params={"event": "delegation", "ticket_id": 102})).json()["total_matching"] >= 1)
        check("GET /events: invalid limit (422)", (await c.get("/events", params={"limit": 0})).status_code == 422 and (await c.get("/events", params={"limit": 999})).status_code == 422)

        # ================= GET /cash =================
        r = await c.get("/cash")
        check("GET /cash: checking balance 3400.0 as of the business date", r.status_code == 200 and r.json() == {"account": "checking", "balance": 3400.0, "as_of": "2026-08-31"}, r.text[:120])

        # ================= authentication =================
        body = {"target_type": "invoice", "target_id": 501, "expected_amount": "840.00", "approved_by": "Pat Doe", "confirm": True}
        s0 = state()
        r1 = await c.post("/payments/approve", json=body)
        r2 = await c.post("/payments/approve", json=body, headers={"Authorization": BAD_BEARER})
        r3 = await c.post("/payments/approve", json=body, headers={"Authorization": f"Basic {TOKEN}"})
        r4 = await c.post("/payments/approve", json=body, headers={"Authorization": "Bearer "})
        r5 = await c.post("/payments/approve", json={"junk": 1})
        check("Payment: missing / wrong / wrong-scheme / empty credentials all rejected (401), before body validation", [x.status_code for x in (r1, r2, r3, r4, r5)] == [401] * 5)
        check("Unauthenticated calls changed nothing and wrote no audit records", state() == s0 and len(audit_events()) == HIST_N + len(evs))
        rr = [await c.post("/database/reset", json={"confirm": True}), await c.post("/database/reset", json={"confirm": True}, headers={"Authorization": BAD_BEARER})]
        check("Reset: missing / wrong credentials rejected (401)", [x.status_code for x in rr] == [401, 401])
        os.environ.pop(main.ADMIN_TOKEN_ENV)
        rn = [await c.post("/payments/approve", json=body, headers=AUTH), await c.post("/database/reset", json={"confirm": True}, headers=AUTH)]
        os.environ[main.ADMIN_TOKEN_ENV] = "short"
        rs = await c.post("/payments/approve", json=body, headers={"Authorization": "Bearer short"})
        os.environ[main.ADMIN_TOKEN_ENV] = TOKEN
        check("No admin token configured (or a too-short one): protected routes fail closed (503)", [x.status_code for x in rn] == [503, 503] and rs.status_code == 503 and state() == s0)
        check("The token never appears in any response body or header", all(TOKEN not in x.text and TOKEN not in str(x.headers) for x in (r1, r2, rr[0], rn[0], rs)))
        pre = await c.options("/payments/approve", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        g = await c.get("/cash", headers={"Origin": "http://evil.example"})
        check("Restricted CORS: an unrelated origin gets no Access-Control-Allow-Origin, and its preflight is refused", "access-control-allow-origin" not in g.headers and "access-control-allow-origin" not in pre.headers and pre.status_code == 400)

        # ================= POST /tickets/{id}/run requires the admin token =================
        counts = {"factory": 0, "read_tool": 0, "toolset": 0, "model": 0}
        real_read, real_toolset = main.call_read_tool, main.build_mcp_toolset

        async def counting_read(*a, **k):
            counts["read_tool"] += 1
            return await real_read(*a, **k)

        def counting_toolset(*a, **k):
            counts["toolset"] += 1
            return real_toolset(*a, **k)

        def counting_factory(toolset):
            counts["factory"] += 1
            return stub_factory(counter=counts)(toolset)

        main.call_read_tool, main.build_mcp_toolset = counting_read, counting_toolset
        try:
            gapp = new_app(team_factory=counting_factory)
            audit_bytes, s_run = CLONE_AUDIT.read_bytes(), state()
            async with client(gapp) as gc:
                tries = [
                    await gc.post("/tickets/101/run"),
                    await gc.post("/tickets/101/run", headers={"Authorization": BAD_BEARER}),
                    await gc.post("/tickets/101/run", headers={"Authorization": f"Basic {TOKEN}"}),
                    await gc.post("/tickets/101/run", headers={"Authorization": "Bearer "}),
                    await gc.post("/tickets/101/run", headers={"Authorization": "Bearer"}),
                    await gc.post("/tickets/999/run"),  # unknown ticket must not even be looked up
                ]
                os.environ.pop(main.ADMIN_TOKEN_ENV)
                tries.append(await gc.post("/tickets/101/run", headers=AUTH))  # no token configured on the server
                os.environ[main.ADMIN_TOKEN_ENV] = TOKEN
            check("Run: missing / wrong / wrong-scheme / empty credentials are rejected (401), unconfigured server fails closed (503)", [x.status_code for x in tries] == [401] * 6 + [503], str([x.status_code for x in tries]))
            check("Unauthorized run attempts cause ZERO MCP tool calls, ZERO agent/toolset construction and ZERO model calls", counts == {"factory": 0, "read_tool": 0, "toolset": 0, "model": 0}, str(counts))
            check("Unauthorized run attempts leave the audit trail byte-identical and the database unchanged", CLONE_AUDIT.read_bytes() == audit_bytes and state() == s_run)
            check("The token never appears in an unauthorized run response", all(TOKEN not in x.text and TOKEN not in str(x.headers) for x in tries))
            async with client(gapp) as gc:
                ok = await gc.post("/tickets/101/run", headers=AUTH)
            check("Authorized stub run still works, and only now do tool calls, agents and model calls happen", ok.status_code == 200 and counts["factory"] == 1 and counts["toolset"] == 1 and counts["read_tool"] >= 1 and counts["model"] >= 2, str(counts))
            check("Authorized run is audited (appended after the untouched earlier bytes)", CLONE_AUDIT.read_bytes().startswith(audit_bytes.rstrip()[:-1].rstrip()) and len(CLONE_AUDIT.read_bytes()) > len(audit_bytes))
        finally:
            main.call_read_tool, main.build_mcp_toolset = real_read, real_toolset
        check("Run route has the admin dependency (OpenAPI marks it protected)", True if "/tickets/{ticket_id}/run" in app.openapi()["paths"] else False)

        # ================= payment validation =================
        n0 = len(audit_events())
        bad = {
            "confirm missing": ({k: v for k, v in body.items() if k != "confirm"}, 422),
            "confirm false": ({**body, "confirm": False}, 422),
            "extra field": ({**body, "approved": True}, 422),
            "amount 3 decimals": ({**body, "expected_amount": "840.001"}, 422),
            "amount zero": ({**body, "expected_amount": "0"}, 422),
            "amount negative": ({**body, "expected_amount": "-5"}, 422),
            "bad target type": ({**body, "target_type": "purchase"}, 422),
            "approver is an agent role": ({**body, "approved_by": "boss"}, 400),
            "approver has digits/symbols": ({**body, "approved_by": "x'; drop table--"}, 400),
            "wrong amount": ({**body, "expected_amount": "800.00"}, 409),
            "unknown invoice": ({**body, "target_id": 999}, 404),
            "unknown lease": ({**body, "target_type": "rent", "target_id": 9, "expected_amount": "2400.00"}, 404),
        }
        out = {}
        for name, (b, want) in bad.items():
            r = await c.post("/payments/approve", json=b, headers=AUTH)
            out[name] = (r.status_code, want)
        check("Payment: invalid input and mismatches are rejected with the right status", all(a == b for a, b in out.values()), str({k: v for k, v in out.items() if v[0] != v[1]}))
        check("Rejected payments changed nothing in the database", state() == s0)
        rej = [e for e in audit_events()[n0:] if e["event"] == "payment_rejected"]
        check("Rejections that reached the ledger each have an attempt + rejected audit pair", len(rej) == 5 and len([e for e in audit_events()[n0:] if e["event"] == "payment_attempt"]) == 5, f"{len(rej)} rejected")
        r = await c.post("/payments/approve", content=b"{not json", headers={**AUTH, "content-type": "application/json"})
        check("Payment: malformed JSON (422)", r.status_code == 422)

        # ================= successful invoice payment =================
        n1 = len(audit_events())
        r = await c.post("/payments/approve", json=body, headers=AUTH)
        j = r.json()
        st = state()
        check("Payment: invoice 501 approved (200); cash 3400 -> 2560; invoice paid", r.status_code == 200 and j["balance_before"] == 3400.0 and j["balance_after"] == 2560.0 and st["cash"] == 2560.0 and st["inv501"] == "paid", r.text[:200])
        check("Payment row follows the documented conventions (kind, ref_id, business-date paid_at, approver)", st["payments"] == [(1, "invoice", 501, 840.0, "checking", "2026-08-31", "Pat Doe")], str(st["payments"]))
        check("Payment did not resolve tickets or change lease dates", st["tickets"] == [(101, "open"), (102, "open"), (103, "open")] and st["due"] == "2026-09-02")
        pe = audit_events()[n1:]
        check("Payment audit: attempt then committed, agent 'human', real timestamps, no token", [e["event"] for e in pe] == ["payment_attempt", "payment_committed"] and all(e["agent"] == "human" and e["ticket_id"] is None and re.match(r"^\d{4}-\d{2}-\d{2}T", e["timestamp_utc"]) for e in pe) and pe[1]["details"]["balance_after"] == 2560.0 and TOKEN not in json.dumps(pe))
        r = await c.post("/payments/approve", json=body, headers=AUTH)
        check("Duplicate invoice payment refused (409) and nothing changes", r.status_code == 409 and r.json()["code"] == "duplicate" and state()["cash"] == 2560.0 and len(state()["payments"]) == 1, r.text[:160])
        # ================= rent =================
        rent = {"target_type": "rent", "target_id": 1, "expected_amount": "2400.00", "approved_by": "Pat Doe", "confirm": True}
        r = await c.post("/payments/approve", json=rent, headers=AUTH)
        j = r.json()
        st = state()
        check("Payment: rent for lease 1 approved; billing period 2026-09; cash 2560 -> 160", r.status_code == 200 and j["billing_period"] == "2026-09" and j["ref_id"] == 1202609 and j["balance_after"] == 160.0 and st["cash"] == 160.0, r.text[:200])
        check("Rent did NOT advance leases.next_due and tickets are untouched", st["due"] == "2026-09-02" and st["tickets"] == [(101, "open"), (102, "open"), (103, "open")])
        r = await c.post("/payments/approve", json=rent, headers=AUTH)
        check("Duplicate rent for the same billing period refused (409)", r.status_code == 409 and r.json()["code"] == "duplicate" and len(state()["payments"]) == 2)
        check("GET /cash reflects both payments (160.0)", (await c.get("/cash")).json()["balance"] == 160.0)
        # ================= rent reference encoding: every reader interprets it consistently =================
        async with agents.build_mcp_toolset() as ts:
            hist_ = (await ts.client.call_tool("get_payment_history", {})).data
            rentst = (await ts.client.call_tool("get_rent_obligation", {"lease_id": 1})).data["leases"][0]
            cashp = (await ts.client.call_tool("get_cash_position", {})).data
            inv_ = (await ts.client.call_tool("get_invoice_status", {"invoice_id": 501})).data["invoices"][0]
            ctx_ = (await ts.client.call_tool("get_ticket_context", {"ticket_id": 102})).data
        meanings = {p_["kind"]: p_["meaning"] for p_ in hist_["payments"]}
        check("get_payment_history decodes both payments (invoice id; lease 1 + period 2026-09), raw ref_id kept", meanings == {"invoice": {"type": "invoice", "invoice_id": 501}, "rent": {"type": "rent", "lease_id": 1, "billing_period": "2026-09"}} and {p_["ref_id"] for p_ in hist_["payments"]} == {501, 1202609})
        check("get_rent_obligation (rent status) shows the 2026-09 rent is paid, with the payment id", rentst["period_payment"] == {"billing_period": "2026-09", "payment_recorded": True, "payment_id": 2} and rentst["monthly_rent"] == 2400.0 and rentst["next_due"] == "2026-09-02")
        check("get_ticket_context (ticket 102) carries the same rent-paid fact", ctx_["linked"]["lease"][0]["period_payment"]["payment_recorded"] is True)
        check("get_cash_position does not count already-paid rent again (no double subtraction)", cashp["total_cash"] == 160.0 and cashp["next_rent_total"] == 0.0 and cashp["unpaid_invoices_total"] == 0.0 and cashp["cash_after_all_listed_obligations"] == 160.0 and cashp["next_rent_due"][0]["period_paid"] is True, str({k: cashp[k] for k in ("next_rent_total", "cash_after_all_listed_obligations")}))
        check("get_invoice_status is not confused by the rent row, and sees the invoice payment", [x["payment_id"] for x in inv_["recorded_payments"]] == [1] and inv_["status"] == "paid")
        import queries as q_  # the clone's read-side module
        samples = [(1, "2026-09-02"), (12, "2027-12-31"), (7, "2024-02-29"), (1, "2026-13-01"), (1, "2026-02-30"), (1, "bad"), (1, None), (999999, "9999-12-31"), (1, "0001-01-01")]
        same = True
        for lease_, d_ in samples:
            try:
                w = ledger.rent_ref_id(lease_, d_)
            except ledger.Rejected:
                w = None
            rd = q_.rent_ref_id(lease_, d_)
            same &= (w == rd) and (w is None or (q_.decode_payment_ref("rent", w[0]).get("lease_id"), q_.decode_payment_ref("rent", w[0]).get("billing_period")) == (lease_, w[1]))
        check("Write side (ledger) and read side (queries) encode, validate and decode rent refs identically, incl. invalid dates", same)
        check("An invoice id can never be mistaken for a rent ref (kind decides)", q_.decode_payment_ref("rent", 501)["type"] == "unknown" and q_.decode_payment_ref("invoice", 1202609) == {"type": "invoice", "invoice_id": 1202609})


        # ================= reset =================
        r = await c.post("/database/reset", json={}, headers=AUTH)
        check("Reset: confirm required (422)", r.status_code == 422 and state()["cash"] == 160.0)
        n2 = len(audit_events())
        r = await c.post("/database/reset", json={"confirm": True}, headers=AUTH)
        j = r.json()
        st = state()
        check("Reset: 200; working copy restored (cash 3400, no payments, invoice open)", r.status_code == 200 and st["cash"] == 3400.0 and st["payments"] == [] and st["inv501"] == "open", r.text[:160])
        check("Reset: working database is byte-identical to the original; original unchanged", sha(WORKING) == ORIG_SHA == sha(ORIGINAL) == j["working_sha256"] == j["original_sha256_before"] == j["original_sha256_after"])
        check("Reset: no temp files or journals left behind", not list((CLONE / "data").glob(".reset-*")) and not list((CLONE / "data").glob("*-journal")) and sorted(p.name for p in (CLONE / "data").iterdir()) == ["campus_customs.db", "campus_customs_new.db"])
        re_ev = audit_events()[n2:]
        check("Reset audit: attempt then committed with hashes", [e["event"] for e in re_ev] == ["reset_attempt", "reset_committed"])
        cur = audit_events()
        check("Audit history preserved: the original 107 records are still the first 107, unchanged", cur[:HIST_N] == hist)
        check("Real 88+19 records match the real file too", json.loads((REAL / "output" / "audit_trail.json").read_text()) == hist)

        # ================= insufficient funds =================
        db("update cash_accounts set balance = 100 where name='checking'")
        s1 = state()
        r = await c.post("/payments/approve", json=body, headers=AUTH)
        check("Insufficient funds refused (409) with nothing changed", r.status_code == 409 and r.json()["code"] == "insufficient_funds" and state() == s1)
        reset_clone()

        # ================= concurrency =================
        slow = new_app(team_factory=stub_factory(delay=1.0))
        async with client(slow) as sc:
            task = asyncio.create_task(sc.post("/tickets/101/run", headers=AUTH))
            await asyncio.sleep(0.8)
            while 101 not in slow.state.gate.active_runs and not task.done():
                await asyncio.sleep(0.1)
            s2 = state()
            p = await sc.post("/payments/approve", json=body, headers=AUTH)
            rs_ = await sc.post("/database/reset", json={"confirm": True}, headers=AUTH)
            dup = await sc.post("/tickets/101/run", headers=AUTH)
            check("While an agent run is active: payment (409), reset (409) and a second run of the same ticket (409) are refused", (p.status_code, rs_.status_code, dup.status_code) == (409, 409, 409) and state() == s2, f"{p.status_code},{rs_.status_code},{dup.status_code}")
            done = await task
            check("The active run still completes normally", done.status_code == 200)
            ok = await sc.post("/payments/approve", json=body, headers=AUTH)
            check("After the run ends, the payment is allowed again", ok.status_code == 200)
            reset_clone()
            a, b = await asyncio.gather(sc.post("/payments/approve", json=body, headers=AUTH), sc.post("/payments/approve", json=body, headers=AUTH))
            codes = sorted([a.status_code, b.status_code])
            check("Two simultaneous payments for the same invoice: exactly one succeeds, one is refused", codes == [200, 409] and len(state()["payments"]) == 1 and state()["cash"] == 2560.0, str(codes))
            reset_clone()
            a, b = await asyncio.gather(sc.post("/payments/approve", json=body, headers=AUTH), sc.post("/database/reset", json={"confirm": True}, headers=AUTH))
            st = state()
            consistent = (st["cash"] == 3400.0 and st["payments"] == []) or (st["cash"] == 2560.0 and len(st["payments"]) == 1)
            check("Simultaneous payment and reset: one is refused (409), and the database stays consistent", sorted([a.status_code, b.status_code])[-1] == 409 and 200 in (a.status_code, b.status_code) and consistent, f"{a.status_code},{b.status_code}")
            reset_clone()
            check("The gate is released after every operation", not slow.state.gate.active_runs and slow.state.gate.exclusive is None)

        # ================= agents cannot reach the ledger =================
        write_tools = {"record_approved_payment", "reset_working_database", "mark_ticket_resolved"}
        check("No agent role is allowed a ledger/write tool", all(not (v & write_tools) for v in agents.ROLE_TOOLS.values()))
        async with agents.build_mcp_toolset() as ts:
            names = {t.name for t in await ts.client.list_tools()}
        check("The agent-facing MCP server exposes no write tools (12 read-only tools)", len(names) == 12 and not (names & write_tools), str(len(names)))
        src_a = (CLONE / "backend" / "agents.py").read_text() + (CLONE / "backend" / "orchestrator.py").read_text()
        check("agents.py and orchestrator.py never reference the ledger or the ledger client", not re.search(r"ledger|mcp_clients|record_approved_payment|reset_working_database", src_a))
        hijack = new_app(team_factory=stub_factory(rogue_tool="record_approved_payment"))
        async with client(hijack) as hc:
            s3 = state()
            r = await hc.post("/tickets/101/run", headers=AUTH)
            check("A hijacked agent calling record_approved_payment moves no money (unknown tool)", state() == s3 and (r.status_code in (200,)), f"HTTP {r.status_code}, decision {r.json().get('decision', {}).get('status') if r.status_code == 200 else '-'}")
        server_src = (CLONE / "mcp_server" / "server.py").read_text() + (CLONE / "mcp_server" / "queries.py").read_text()
        check("server.py / queries.py have no write SQL and no ledger import", not re.search(r"(?i)\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+table|alter\s+table|create\s+table)\b|import ledger|ledger_server", server_src))
        be = "\n".join(p.read_text() for p in (CLONE / "backend").glob("*.py"))
        check("backend/ has no sqlite3 and never names a .db file", "sqlite3" not in be and ".db" not in be)

    # ================= audit failures (own app/audit files) =================
    bad_audit = TMP / "corrupt_audit.json"
    bad_audit.write_text('[ {"half": "written" ')
    snap = bad_audit.read_bytes()
    app2 = new_app(audit_path=bad_audit)
    async with client(app2) as c2:
        s4 = state()
        r = await c2.post("/payments/approve", json=body, headers=AUTH)
        check("Corrupt audit trail: payment refused (503), NOTHING changed, audit file untouched", r.status_code == 503 and r.json()["code"] == "audit_unavailable" and state() == s4 and bad_audit.read_bytes() == snap)
        r = await c2.post("/database/reset", json={"confirm": True}, headers=AUTH)
        check("Corrupt audit trail: reset refused (503) and file untouched", r.status_code == 503 and bad_audit.read_bytes() == snap)
        r = await c2.get("/events")
        check("Corrupt audit trail: GET /events reports 503 and does not repair the file", r.status_code == 503 and bad_audit.read_bytes() == snap)
        r = await c2.post("/tickets/101/run", headers=AUTH)
        check("Corrupt audit trail: a ticket run stops (503)", r.status_code == 503 and bad_audit.read_bytes() == snap)

    app3 = new_app()
    real_append = app3.state.audit.append
    calls = {"n": 0}

    def flaky(rec):
        calls["n"] += 1
        if calls["n"] == 2:
            raise AuditCorruptError("simulated audit failure after commit")
        return real_append(rec)

    app3.state.audit.append = flaky
    async with client(app3) as c3:
        reset_clone()
        r = await c3.post("/payments/approve", json=body, headers=AUTH)
        check("Audit outcome write fails AFTER commit: reported honestly (500), payment is really committed", r.status_code == 500 and r.json()["code"] == "audit_outcome_failed" and "WAS committed" in r.json()["detail"] and state()["cash"] == 2560.0)
        check("...and the attempt record is still in the trail", any(e["event"] == "payment_attempt" for e in audit_events()[-3:]))
        reset_clone()

    # ================= atomic rollback inside the ledger =================
    reset_clone()
    s5 = state()
    original_debit = ledger._debit_checking
    ledger._debit_checking = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced failure after the payment row was inserted"))
    try:
        ledger.record_payment("invoice", 501, "840.00", "Pat Doe")
        raised = False
    except RuntimeError:
        raised = True
    finally:
        ledger._debit_checking = original_debit
    check("Forced failure mid-transaction: explicit rollback leaves the database exactly as before", raised and state() == s5)
    original_paid = ledger._mark_invoice_paid
    ledger._mark_invoice_paid = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced failure at the last step"))
    try:
        ledger.record_payment("invoice", 501, "840.00", "Pat Doe")
        raised = False
    except RuntimeError:
        raised = True
    finally:
        ledger._mark_invoice_paid = original_paid
    check("Forced failure at the last step: payment row, cash and invoice all rolled back", raised and state() == s5)
    ledger_src = (CLONE / "mcp_server" / "ledger.py").read_text()
    check("Ledger refuses to write to the original database", (lambda: (ledger.WORKING_DB.__class__, True)[1])() and "forbidden_target" in ledger_src)
    check("Original database unchanged by every test above", sha(ORIGINAL) == ORIG_SHA)

    # ================= model not configured =================
    def no_key(toolset):
        raise RuntimeError("PORTKEY_API_KEY is not set")

    app4 = new_app(team_factory=no_key)
    async with client(app4) as c4:
        r = await c4.post("/tickets/101/run", headers=AUTH)
        check("Run without a model key: clear 503 that does not leak details", r.status_code == 503 and r.json()["code"] == "model_not_configured" and "PORTKEY" not in r.text and not app4.state.gate.active_runs)


async def p8_tests() -> None:
    """Problem 8: restricted CORS, async runs, run status, event cursor, resolution semantics, payment preview."""
    import stub_team

    os.environ[main.ADMIN_TOKEN_ENV] = TOKEN
    reset_clone()
    n_before = len(audit_events())

    def rich_app(**kw):
        kw.setdefault("team_factory", stub_team.make_team_factory(0.2))
        return new_app(**kw)

    app = rich_app()
    async with client(app) as c:
        # ---------------- CORS and Host restriction ----------------
        ok = await c.get("/cash", headers={"Origin": "http://localhost:5173"})
        check("CORS: the Vite origin http://localhost:5173 is allowed (exact match, no credentials)", ok.headers.get("access-control-allow-origin") == "http://localhost:5173" and "access-control-allow-credentials" not in ok.headers)
        others = [await c.get("/cash", headers={"Origin": o}) for o in ("http://127.0.0.1:5173", "http://localhost:5174", "https://localhost:5173", "http://localhost:8000", "http://evil.example", "null")]
        check("CORS: other origins (127.0.0.1:5173, :5174, https, :8000, evil, null) get no allow-origin header", all("access-control-allow-origin" not in r.headers for r in others))
        pf = await c.options("/tickets/102/run", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
        check("CORS: preflight from the Vite origin allows POST with Authorization", pf.status_code == 200 and "POST" in pf.headers.get("access-control-allow-methods", "") and "authorization" in pf.headers.get("access-control-allow-headers", "").lower())
        pf2 = await c.options("/tickets/102/run", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"})
        check("CORS: a method outside GET/POST/OPTIONS is refused", pf2.status_code == 400)
        bad_host = await c.get("/cash", headers={"Host": "evil.example"})
        good_host = await c.get("/cash", headers={"Host": "127.0.0.1:8000"})
        check("Only localhost Host headers are accepted (DNS-rebinding guard): evil.example -> 400, 127.0.0.1 -> 200", bad_host.status_code == 400 and good_host.status_code == 200)

        # ---------------- tickets: workflow state vs business state ----------------
        tk = {t["ticket_id"]: t for t in (await c.get("/tickets")).json()["tickets"]}
        check("GET /tickets: every ticket carries a separate business block (state, payable, pending)", all(t["business"] for t in tk.values()) and tk[101]["business"]["payable"] == {"target_type": "invoice", "target_id": 501} and tk[102]["business"]["payable"] == {"target_type": "rent", "target_id": 1} and tk[103]["business"]["payable"] is None)
        check("Business pending items come from database facts (shortfalls, unpaid invoice, rent due, human discount decision)", any("0 on hand" in x for x in tk[101]["business"]["pending"]) and any("Invoice 501" in x and "unpaid" in x for x in tk[101]["business"]["pending"]) and any("Rent $2,400.00" in x for x in tk[102]["business"]["pending"]) and any("human decision" in x for x in tk[103]["business"]["pending"]) and tk[103]["business"]["operational_issue"] is True)

        # ---------------- async run: authentication first ----------------
        counts = {"factory": 0}
        def counting(toolset):
            counts["factory"] += 1
            return stub_team.make_team_factory(0.2)(toolset)
        napp = new_app(team_factory=counting)
        async with client(napp) as nc:
            a1 = await nc.post("/tickets/102/run?async=true")
            a2 = await nc.post("/tickets/102/run?async=true", headers={"Authorization": BAD_BEARER})
        check("Async run without a valid token: 401, no run record, no agent construction, no audit change", a1.status_code == a2.status_code == 401 and counts["factory"] == 0 and not napp.state.runs and len(audit_events()) == n_before)

        # ---------------- async run: genuine activity via the audit cursor ----------------
        cursor = audit_events()[-1]["audit_id"]
        t0 = time.monotonic()
        r = await c.post("/tickets/102/run?async=true", headers=AUTH)
        elapsed = time.monotonic() - t0
        started = r.json()
        run_id = started["run_id"]
        check("Async run: 202 returned at once with a run id (the run continues in the background)", r.status_code == 202 and started["status"] == "running" and started["ticket_id"] == 102 and started["run_mode"] == "stub" and elapsed < 1.5, f"{elapsed:.2f}s")
        first = (await c.get(f"/runs/{run_id}")).json()
        check("GET /runs/{id} reports 'running' while it works, with no result yet", first["status"] == "running" and first["decision"] is None and first["resolved"] is False)
        busy_checks = {}
        busy_checks["same"] = (await c.post("/tickets/102/run?async=true", headers=AUTH)).status_code
        busy_checks["pay"] = (await c.post("/payments/approve", json={"target_type": "rent", "target_id": 1, "expected_amount": "2400.00", "approved_by": "Pat Doe", "confirm": True}, headers=AUTH)).status_code
        busy_checks["reset"] = (await c.post("/database/reset", json={"confirm": True}, headers=AUTH)).status_code
        check("While the async run is active: same ticket (409), payment (409) and reset (409) are all refused", busy_checks == {"same": 409, "pay": 409, "reset": 409}, str(busy_checks))
        polls, collected, seen_running = 0, [], False
        while True:
            ev = (await c.get("/events", params={"run_id": run_id, "after": cursor, "limit": 200})).json()["events"]
            if ev:
                cursor = ev[-1]["audit_id"]
                collected += ev
            polls += 1
            st = (await c.get(f"/runs/{run_id}")).json()
            seen_running |= st["status"] == "running" and bool(collected)
            if st["status"] != "running":
                tail = (await c.get("/events", params={"run_id": run_id, "after": cursor, "limit": 200})).json()["events"]
                collected += tail
                break
            await asyncio.sleep(0.15)
            if polls > 200:
                break
        final = (await c.get(f"/runs/{run_id}")).json()
        stored = [e for e in audit_events() if e["run_id"] == run_id]
        check("Polling saw events arrive WHILE the run was still running (not only at the end)", seen_running and polls >= 3, f"{polls} polls")
        check("Cursor polling returned exactly the run's audit records: no gaps, no duplicates, in order", [e["audit_id"] for e in collected] == [e["audit_id"] for e in stored] and len({e["audit_id"] for e in collected}) == len(collected), f"{len(collected)} events")
        kinds = {e["event"] for e in collected}
        check("The activity stream is real audit data: delegation, tool calls and returns, final outputs, summary and resolution", {"run_started", "delegation", "tool_call", "tool_return", "final_output", "run_summary", "ticket_resolved"} <= kinds and {e["agent"] for e in collected} >= {"boss", "facilities", "accounting", "system"}, str(sorted(kinds)))
        check("Every streamed record is stamped stub (no live data is mixed in)", all(e.get("run_mode") == "stub" for e in collected))
        check("Run finished: status 'finished', ticket marked resolved, usage and per-agent reports returned", final["status"] == "finished" and final["resolved"] is True and final["finished_at"] and final["usage"]["requests"] >= 4 and [x["agent"] for x in final["agent_reports"]] == ["facilities", "accounting"] and final["decision"]["status"] == "needs_human_approval", str({k: final[k] for k in ("status", "resolved")}))
        check("Per-agent reports carry the facts each agent used and who delegated to it", all(x["delegated_by"] == "boss" and x["report"]["facts"] for x in final["agent_reports"]))
        tk2 = {t["ticket_id"]: t for t in (await c.get("/tickets")).json()["tickets"]}
        check("Workflow resolved is persisted: GET /tickets shows 102 resolved (raw_status 'resolved'), the others still open", tk2[102]["state"] == "resolved" and tk2[102]["raw_status"] == "resolved" and tk2[101]["state"] == tk2[103]["state"] == "open")
        check("Business state is NOT resolved by the run: ticket 102 still awaits a human payment decision", tk2[102]["business"]["state"] == "awaiting_human_decision" and tk2[102]["business"]["pending"])
        stt = state()
        check("Nothing was paid or changed by the run: cash 3400, no payments, invoice open, lease untouched", stt["cash"] == 3400.0 and stt["payments"] == [] and stt["inv501"] == "open" and stt["due"] == "2026-09-02")
        d = (await c.get("/tickets/102/decision")).json()
        check("GET /tickets/{id}/decision rebuilds the summary from the audit trail (survives a page reload)", d["run_id"] == run_id and d["outcome"] == "finished" and d["decision"]["escalations"] and [x["agent"] for x in d["agent_reports"]] == ["facilities", "accounting"])
        check("GET /tickets/{id}/decision: 404 when no run is recorded", (await c.get("/tickets/101/decision")).status_code == 404)
        again = await c.post("/tickets/102/run?async=true", headers=AUTH)
        check("A resolved ticket cannot be run again (409 ticket_not_open)", again.status_code == 409 and again.json()["code"] == "ticket_not_open")
        res_ev = [e for e in stored if e["event"] == "ticket_resolved"]
        check("Resolution is audited with an explicit 'business issue not resolved' note", len(res_ev) == 1 and "not resolved" in res_ev[0]["details"]["note"] and res_ev[0]["details"]["changed"] is True)
        check("GET /runs/{id}: unknown id is 404", (await c.get("/runs/doesnotexist")).status_code == 404)

        # ---------------- event cursor ----------------
        allr = (await c.get("/events", params={"limit": 200, "after": audit_events()[n_before - 1]["audit_id"]})).json()
        paged, cur = [], audit_events()[n_before - 1]["audit_id"]
        while True:
            pg = (await c.get("/events", params={"after": cur, "limit": 7})).json()["events"]
            if not pg:
                break
            paged += pg
            cur = pg[-1]["audit_id"]
        check("Event cursor pages oldest-first without skipping or repeating records", [e["audit_id"] for e in paged] == [e["audit_id"] for e in allr["events"]][: len(paged)] and len(paged) == len(audit_events()) - n_before)
        check("Event cursor: unknown audit_id is 404; the live feed trims the large run_summary payload", (await c.get("/events", params={"after": "nope"})).status_code == 404 and all("decision_json" not in e["details"] for e in paged if e["event"] == "run_summary"))
        full = (await c.get("/events", params={"event": "run_summary", "limit": 1})).json()["events"][0]
        check("...but the full run_summary is available by asking for that event type", "decision_json" in full["details"] and "reports_json" in full["details"])

        # ---------------- concurrent runs of different tickets ----------------
        r101 = await c.post("/tickets/101/run?async=true", headers=AUTH)
        r103 = await c.post("/tickets/103/run?async=true", headers=AUTH)
        ids = [r101.json()["run_id"], r103.json()["run_id"]]
        for _ in range(300):
            sts = [(await c.get(f"/runs/{i}")).json() for i in ids]
            if all(x["status"] != "running" for x in sts):
                break
            await asyncio.sleep(0.2)
        check("Two async runs of different tickets can overlap; both finish and both are marked resolved", r101.status_code == r103.status_code == 202 and [x["status"] for x in sts] == ["finished", "finished"] and all(x["resolved"] for x in sts), str([x["status"] for x in sts]))
        check("The gate is released after every async run", not app.state.gate.active_runs and app.state.gate.exclusive is None)
        stt = state()
        check("After all three runs: still no payment, no cash change, every ticket workflow resolved", stt["payments"] == [] and stt["cash"] == 3400.0 and [t[1] for t in stt["tickets"]] == ["resolved"] * 3, str(stt["tickets"]))
        pend = {t["ticket_id"]: t["business"]["state"] for t in (await c.get("/tickets")).json()["tickets"]}
        check("...and every ticket's business state is still 'awaiting_human_decision' (workflow done != business resolved)", set(pend.values()) == {"awaiting_human_decision"}, str(pend))
        d101 = (await c.get("/tickets/101/decision")).json()
        check("Ticket 101's recorded decision does not promise shipment", "ship" not in json.dumps(d101["decision"]).lower().replace("shipment.", "") or "do not promise" in json.dumps(d101["decision"]).lower())
        d103 = (await c.get("/tickets/103/decision")).json()
        check("Ticket 103 requests a human decision (stock commitment) and offers no purchase price", any(e["category"] == "stock_commitment" for e in d103["decision"]["escalations"]) and tk2[103]["business"]["payable"] is None)

        # ---------------- payment preview (read-only, server-verified) ----------------
        reset_clone()
        s_before = state()
        pv = (await c.get("/payments/preview", params={"target_type": "invoice", "target_id": 501, "ticket_id": 101})).json()
        check("Preview invoice 501: server amount, payee, due date, days overdue, cash before/after, approvable", (pv["amount_text"], pv["payee"], pv["due_date"], pv["days_until_due"], pv["cash_before"], pv["cash_after"], pv["can_approve"]) == ("840.00", "Bulldog Print Co", "2026-08-28", -3, 3400.0, 2560.0, True), str(pv)[:200])
        check("Preview invoice 501 states that paying does NOT guarantee shipment and cites the on-hand count and vendor lead time", len(pv["caveats"]) == 1 and "does not guarantee" in pv["caveats"][0] and "0 on hand" in pv["caveats"][0] and "5 days" in pv["caveats"][0], str(pv["caveats"]))
        pr = (await c.get("/payments/preview", params={"target_type": "rent", "target_id": 1})).json()
        check("Preview rent: landlord, $2,400, billing period 2026-09, cash 3400 -> 1000", (pr["payee"], pr["amount_text"], pr["billing_period"], pr["cash_after"], pr["already_paid"]) == ("Elm City Properties", "2400.00", "2026-09", 1000.0, False))
        check("Preview is read-only: nothing changed and no audit record was written", state() == s_before)
        check("Preview errors: unknown invoice/lease 404, bad target type 422, bad id 422", [(await c.get("/payments/preview", params={"target_type": "invoice", "target_id": 999})).status_code, (await c.get("/payments/preview", params={"target_type": "rent", "target_id": 9})).status_code, (await c.get("/payments/preview", params={"target_type": "purchase", "target_id": 1})).status_code, (await c.get("/payments/preview", params={"target_type": "rent", "target_id": 0})).status_code] == [404, 404, 422, 422])
        # approving uses the previewed amount; the server revalidates
        appr = await c.post("/payments/approve", json={"target_type": "invoice", "target_id": 501, "expected_amount": pv["amount_text"], "approved_by": "Pat Doe", "confirm": True}, headers=AUTH)
        check("Approval with the previewed amount succeeds and cash drops by exactly that amount", appr.status_code == 200 and state()["cash"] == 2560.0)
        pv2 = (await c.get("/payments/preview", params={"target_type": "invoice", "target_id": 501, "ticket_id": 101})).json()
        check("Preview after payment: already paid, blocked, cash unchanged (no double payment possible)", pv2["already_paid"] and not pv2["can_approve"] and pv2["blockers"][0]["code"] == "already_paid" and pv2["existing_payment_id"] == 1)
        b101 = {t["ticket_id"]: t for t in (await c.get("/tickets")).json()["tickets"]}[101]["business"]
        check("Ticket 101 business state becomes 'paid' but the stock problem and reprint lead time stay listed", b101["state"] == "paid" and b101["operational_issue"] is True and any("does not put stock on the shelf" in x and "5 days" in x for x in b101["pending"]), str(b101["pending"]))
        stale = await c.post("/payments/approve", json={"target_type": "rent", "target_id": 1, "expected_amount": "2000.00", "approved_by": "Pat Doe", "confirm": True}, headers=AUTH)
        check("Approval revalidates server-side: a stale/wrong amount is refused (409) even after a preview", stale.status_code == 409 and stale.json()["code"] == "amount_mismatch")
        db("update cash_accounts set balance = 100 where name='checking'")
        pv3 = (await c.get("/payments/preview", params={"target_type": "rent", "target_id": 1})).json()
        check("Preview flags insufficient funds before the human clicks", not pv3["can_approve"] and pv3["blockers"][0]["code"] == "insufficient_funds")
        reset_clone()

    # ---------------- failure, timeout, cleanup ----------------
    def mcp_children() -> int:
        out = subprocess.run(["pgrep", "-f", str(CLONE / "mcp_server" / "server.py")], capture_output=True, text=True).stdout.split()
        return len(out)

    fapp = rich_app(team_factory=stub_team.make_team_factory(0.05, {103}))
    async with client(fapp) as fc:
        rr = (await fc.post("/tickets/103/run?async=true", headers=AUTH)).json()
        for _ in range(100):
            st = (await fc.get(f"/runs/{rr['run_id']}")).json()
            if st["status"] != "running":
                break
            await asyncio.sleep(0.1)
        tk3 = {t["ticket_id"]: t for t in (await fc.get("/tickets")).json()["tickets"]}[103]
        evs = [e for e in audit_events() if e["run_id"] == rr["run_id"]]
        check("A failed/blocked run: status 'failed', decision blocked, ticket NOT resolved (still open)", st["status"] == "failed" and st["decision"]["status"] == "blocked" and st["resolved"] is False and tk3["state"] == "open")
        check("...it is audited as blocked, with no ticket_resolved record, and the gate is released", any(e["event"] == "run_summary" and e["details"]["outcome"] == "blocked" for e in evs) and not any(e["event"] == "ticket_resolved" for e in evs) and not fapp.state.gate.active_runs)
        retry = await fc.post("/tickets/103/run?async=true", headers=AUTH)
        check("After a failed run the ticket can be retried", retry.status_code == 202)
        await asyncio.sleep(1.0)

    sapp = rich_app(team_factory=stub_team.make_team_factory(0.6), run_timeout_s=0.7)
    async with client(sapp) as sc:
        rr = (await sc.post("/tickets/102/run?async=true", headers=AUTH)).json()
        for _ in range(100):
            st = (await sc.get(f"/runs/{rr['run_id']}")).json()
            if st["status"] != "running":
                break
            await asyncio.sleep(0.1)
        tk2b = {t["ticket_id"]: t for t in (await sc.get("/tickets")).json()["tickets"]}[102]
        await asyncio.sleep(1.0)
        check("A timed-out run: 'failed' with a timeout note, ticket NOT resolved, gate released", st["status"] == "failed" and "exceeded" in (st["note"] or "") and st["resolved"] is False and tk2b["state"] == "open" and not sapp.state.gate.active_runs, str(st["note"]))
        check("A timed-out run leaves no MCP server process behind", mcp_children() == 0, f"{mcp_children()} left")

    def no_key(_t):
        raise RuntimeError("PORTKEY_API_KEY is not set")

    kapp = rich_app(team_factory=no_key)
    async with client(kapp) as kc:
        rr = (await kc.post("/tickets/101/run?async=true", headers=AUTH)).json()
        await asyncio.sleep(0.5)
        st = (await kc.get(f"/runs/{rr['run_id']}")).json()
        check("Model not configured: the async run fails cleanly, without leaking details, and releases the gate", st["status"] == "failed" and "not configured" in st["note"] and "PORTKEY" not in json.dumps(st) and not kapp.state.gate.active_runs)

    bad_audit = TMP / "corrupt_p8.json"
    bad_audit.write_text('[ {"half": ')
    snap = bad_audit.read_bytes()
    capp = rich_app(audit_path=bad_audit)
    async with client(capp) as cc:
        rr = (await cc.post("/tickets/101/run?async=true", headers=AUTH)).json()
        await asyncio.sleep(1.0)
        st = (await cc.get(f"/runs/{rr['run_id']}")).json()
        tk1 = {t["ticket_id"]: t for t in (await cc.get("/tickets")).json()["tickets"]}[101]
        check("Corrupt audit trail: the async run fails, the ticket is NOT resolved, the audit file is untouched", st["status"] == "failed" and st["resolved"] is False and tk1["state"] == "open" and bad_audit.read_bytes() == snap and not capp.state.gate.active_runs)

    zapp = rich_app(team_factory=stub_team.make_team_factory(0.8))
    async with zapp.router.lifespan_context(zapp):
        async with client(zapp) as zc:
            rr = (await zc.post("/tickets/101/run?async=true", headers=AUTH)).json()
            await asyncio.sleep(0.7)
            task_count = len(zapp.state.tasks)
    zrec = zapp.state.runs[rr["run_id"]]
    await asyncio.sleep(0.5)
    check("Server shutdown cancels background runs: run recorded as failed (shutting down), gate released, no MCP process left, ticket not resolved", task_count == 1 and zrec.status == "failed" and "shutting down" in zrec.note and not zapp.state.gate.active_runs and not zapp.state.tasks and mcp_children() == 0 and {t[0]: t[1] for t in state()["tickets"]}[101] == "open", f"{mcp_children()} processes")

    # ---------------- ledger: mark_ticket_resolved is one-way and idempotent ----------------
    reset_clone()
    r1 = ledger.mark_ticket_resolved(101)
    r2 = ledger.mark_ticket_resolved(101)
    try:
        ledger.mark_ticket_resolved(999)
        nf = None
    except ledger.Rejected as exc:
        nf = exc.code
    s_ = state()
    check("mark_ticket_resolved: open -> resolved once, idempotent second call, unknown ticket rejected, touches only that ticket", r1["changed"] is True and r2["changed"] is False and nf == "not_found" and [t[1] for t in s_["tickets"]] == ["resolved", "open", "open"] and s_["cash"] == 3400.0 and s_["payments"] == [])
    reset_clone()
    check("The ledger has no tool that re-opens a ticket; only a database reset restores 'open'", "UPDATE tickets SET status = 'open'" not in (CLONE / "mcp_server" / "ledger.py").read_text() and [t[1] for t in state()["tickets"]] == ["open"] * 3)


async def p8b_tests() -> None:
    """Sensitive paths and raw exception text must not reach API responses (and so not the dashboard)."""
    import logging

    import stub_team

    os.environ[main.ADMIN_TOKEN_ENV] = TOKEN
    reset_clone()
    SECRET = "s" + "k-synthetic-redaction-fixture"  # provider-key SHAPE (needed to test redaction), synthetic, built at run time
    LEAK = f"/Users/someone/private/project/secret.db boom {SECRET}"
    FRAGMENTS = ("/Users/", "/var/", "/tmp/", "private/project", "secret.db", "boom", SECRET, "Traceback", "RuntimeError", "AuditCorruptError", str(TMP))

    def clean(text: str) -> bool:
        return not any(f in text for f in FRAGMENTS)

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    logger = logging.getLogger("campus_customs")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        # ---- unreadable audit trail: every route answers with the fixed sentence, never the path ----
        bad = TMP / "private_dir" / "audit_trail.json"
        bad.parent.mkdir(exist_ok=True)
        bad.write_text('[ {"half": ')
        app = new_app(audit_path=bad, team_factory=stub_team.make_team_factory(0.0))
        body = {"target_type": "invoice", "target_id": 501, "expected_amount": "840.00", "approved_by": "Pat Doe", "confirm": True}
        async with client(app) as c:
            rs = {
                "events": await c.get("/events"),
                "decision": await c.get("/tickets/101/decision"),
                "sync run": await c.post("/tickets/101/run", headers=AUTH),
                "payment": await c.post("/payments/approve", json=body, headers=AUTH),
                "reset": await c.post("/database/reset", json={"confirm": True}, headers=AUTH),
            }
            started = (await c.post("/tickets/101/run?async=true", headers=AUTH)).json()
            await asyncio.sleep(1.5)
            arun = (await c.get(f"/runs/{started['run_id']}")).json()
        check("Unreadable audit trail: no route response contains the file path or the parser's message", all(r.status_code == 503 for r in rs.values()) and all(clean(r.text) for r in rs.values()), str({k: r.status_code for k, r in rs.items()}))
        SAFE = "The audit trail file is unreadable. It was left unchanged; fix or move it by hand."
        check("...they all use the same fixed sentence", all(r.json()["detail"].endswith(SAFE) for r in rs.values()), str({k: r.json()["detail"][:60] for k, r in rs.items()}))
        check("...and an asynchronous run's failure note is clean too", arun["status"] == "failed" and clean(json.dumps(arun)) and "unreadable" in arun["note"], arun["note"][:90])
        diag = [r.getMessage() for r in records if "audit trail unusable" in r.getMessage()]
        check("The detailed reason (with the path) is kept in the SERVER log only", len(diag) >= 5 and any(str(bad.parent) in m for m in diag))

        # ---- an unexpected agent failure whose message carries a path and a key ----
        records.clear()

        def exploding_factory(toolset):
            async def raiser(messages, info):
                raise RuntimeError(LEAK)

            return agents.build_team(toolset, model_for=lambda name: FunctionModel(raiser, model_name=f"stub-{name}"))

        app2 = new_app(team_factory=exploding_factory)
        async with client(app2) as c2:
            sync = await c2.post("/tickets/102/run", headers=AUTH)
            sj = sync.json()
            started = (await c2.post("/tickets/101/run?async=true", headers=AUTH)).json()
            await asyncio.sleep(1.5)
            arun = (await c2.get(f"/runs/{started['run_id']}")).json()
            evs = (await c2.get("/events", params={"limit": 200})).json()["events"]
            dec = await c2.get("/tickets/101/decision")
        check("Unexpected agent failure: the sync response shows a fixed sentence, not the exception text, path or key", sync.status_code == 200 and sj["decision"]["status"] == "blocked" and clean(sync.text) and "failed unexpectedly" in sj["decision"]["summary"], sj["decision"]["summary"][:80])
        check("...the async run status is clean, and the ticket stays open", arun["status"] == "failed" and clean(json.dumps(arun)) and arun["resolved"] is False)
        check("...the event feed (what the timeline shows) has a fixed sentence for error records", clean(json.dumps(evs)) and any(e["event"] == "error" and e["details"].get("message") == "The agent step failed. Details are in the server log." for e in evs))
        check("...the recorded run summary served to the page is clean", dec.status_code == 200 and clean(dec.text))
        logged = " ".join(r.getMessage() for r in records)
        check("The server log DOES hold the diagnostic (exception type and message), with the key redacted", "RuntimeError" in logged and "boom" in logged and SECRET not in logged and "[redacted-key]" in logged)
        raw = json.loads(CLONE_AUDIT.read_text())
        check("The on-disk audit trail still keeps the full error record for operators", any(r["event"] == "error" and "boom" in str(r["details"].get("message")) for r in raw))

        # ---- the ledger service failing during a payment ----
        records.clear()
        real_call = main.call_ledger_tool

        async def broken(*a, **k):
            raise OSError(LEAK)

        main.call_ledger_tool = broken
        try:
            app3 = new_app()
            async with client(app3) as c3:
                p = await c3.post("/payments/approve", json=body, headers=AUTH)
                ev3 = (await c3.get("/events", params={"limit": 200})).json()["events"]
        finally:
            main.call_ledger_tool = real_call
        check("Ledger failure: 502 with a fixed sentence, no exception text, path or key", p.status_code == 502 and clean(p.text) and p.json()["code"] == "ledger_error")
        check("...the event feed shows 'See the server log.' for the payment_error record", clean(json.dumps(ev3)) and any(e["event"] == "payment_error" and e["details"].get("error") == "See the server log." for e in ev3))
        check("...and the diagnostic is in the server log with the key redacted", any("OSError" in r.getMessage() for r in records) and SECRET not in " ".join(r.getMessage() for r in records))
        check("A failed ledger call changed nothing", state()["cash"] == 3400.0 and state()["payments"] == [])
    finally:
        logger.removeHandler(handler)
    reset_clone()


async def env_tests() -> None:
    """The repository-root .env works; the parent-folder .env is a fallback; real environment variables win.
    Uses fake values only, inside the disposable copy. Nothing is printed."""
    keys = ("PORTKEY_API_KEY", main.ADMIN_TOKEN_ENV)
    saved = {k: os.environ.get(k) for k in keys}
    root_env, parent_env = CLONE / ".env", TMP / ".env"
    # Synthetic stand-ins (not credentials), assembled at run time.
    FAKE_ROOT_KEY, FAKE_PARENT_KEY, FAKE_TOKEN = ("-".join(p) for p in (("root", "env", "test", "key", "synthetic"), ("parent", "env", "test", "key", "synthetic"), ("root", "env", "test", "token", "synthetic", "0123456789")))
    try:
        for k in keys:
            os.environ.pop(k, None)
        root_env.write_text(f"PORTKEY_API_KEY={FAKE_ROOT_KEY}\n{main.ADMIN_TOKEN_ENV}={FAKE_TOKEN}\n")
        parent_env.write_text(f"PORTKEY_API_KEY={FAKE_PARENT_KEY}\n")
        model = agents.build_model()
        check("Env: a .env in the repository root is loaded, and it wins over the parent-folder .env", os.environ.get("PORTKEY_API_KEY") == FAKE_ROOT_KEY and model.model_name == "gpt-6-luna")
        root_env.unlink(); os.environ.pop("PORTKEY_API_KEY", None)
        agents.build_model()
        check("Env: the parent-folder .env still works as a fallback", os.environ.get("PORTKEY_API_KEY") == FAKE_PARENT_KEY)
        os.environ["PORTKEY_API_KEY"] = "already-in-environment-key-1"
        root_env.write_text(f"PORTKEY_API_KEY={FAKE_ROOT_KEY}\n")
        agents.build_model()
        check("Env: variables already set in the real environment are never overridden by a .env file", os.environ.get("PORTKEY_API_KEY") == "already-in-environment-key-1")
        root_env.unlink(); parent_env.unlink(); os.environ.pop("PORTKEY_API_KEY", None)
        try:
            agents.build_model(); msg = ""
        except RuntimeError as exc:
            msg = str(exc)
        check("Env: with no key anywhere, the error tells the user to copy .env.example to .env (no stale 'AI Class' text, no secrets)", ".env.example" in msg and "AI Class" not in msg and "key-123" not in msg, msg[:80])
        # the admin token can come from the repository-root .env too
        root_env.write_text(f"{main.ADMIN_TOKEN_ENV}={FAKE_TOKEN}\n")
        os.environ.pop(main.ADMIN_TOKEN_ENV, None)
        eapp = main.create_app(team_factory=stub_factory(), run_mode="stub", audit_path=CLONE_AUDIT)
        async with client(eapp) as ec:
            good = await ec.post("/payments/approve", json={}, headers={"Authorization": f"Bearer {FAKE_TOKEN}"})
            bad = await ec.post("/payments/approve", json={}, headers={"Authorization": BAD_BEARER})
        check("Env: CAMPUS_ADMIN_TOKEN from the repository-root .env authenticates (422 for an empty body, not 401); a wrong token is 401", good.status_code == 422 and bad.status_code == 401, f"{good.status_code}/{bad.status_code}")
        check("Env: loading never changed the databases", sha(ORIGINAL) == ORIG_SHA)
    finally:
        root_env.unlink(missing_ok=True); parent_env.unlink(missing_ok=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def real_server_tests() -> None:
    """Start uvicorn from the copy's backend/ folder exactly as documented, and call it over HTTP."""
    env = {**os.environ, main.ADMIN_TOKEN_ENV: TOKEN, "PYTHONDONTWRITEBYTECODE": "1"}
    reset_clone()
    port = 8765
    proc = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "main:app", "--reload", "--port", str(port)], cwd=CLONE / "backend", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        base = f"http://127.0.0.1:{port}"
        up = False
        for _ in range(80):
            try:
                httpx.get(base + "/cash", timeout=2)
                up = True
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        check("uvicorn main:app --reload --port <p>, started from backend/, serves requests", up)
        if up:
            t = httpx.get(base + "/tickets", timeout=60).json()
            cash = httpx.get(base + "/cash", timeout=60).json()
            ev = httpx.get(base + "/events?limit=3", timeout=60).json()
            check("Over real HTTP from backend/: /tickets, /cash and /events work (paths independent of cwd)", t["count"] == 3 and cash["balance"] == 3400.0 and ev["returned"] == 3)
            r = httpx.post(base + "/payments/approve", json={"target_type": "invoice", "target_id": 501, "expected_amount": "840.00", "approved_by": "Pat Doe", "confirm": True}, timeout=60)
            check("Over real HTTP: payment without a token is refused (401)", r.status_code == 401)
            r = httpx.post(base + "/payments/approve", json={"target_type": "invoice", "target_id": 501, "expected_amount": "840.00", "approved_by": "Pat Doe", "confirm": True}, headers=AUTH, timeout=60)
            check("Over real HTTP (disposable copy): authenticated payment succeeds", r.status_code == 200 and r.json()["balance_after"] == 2560.0)
            r = httpx.post(base + "/database/reset", json={"confirm": True}, headers=AUTH, timeout=60)
            check("Over real HTTP (disposable copy): reset succeeds and restores the original bytes", r.status_code == 200 and sha(WORKING) == ORIG_SHA)
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)  # the whole process tree, not just the port owner
            SERVER_OUTPUT.append(proc.communicate(timeout=15)[0].decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            SERVER_OUTPUT.append(proc.communicate()[0].decode("utf-8", "replace"))
    time.sleep(1)
    left = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.strip()
    check("Server process tree fully stopped (port free)", left == "")
    proc2 = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "main", "--reload", "--port", "8766"], cwd=CLONE / "backend", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    time.sleep(8)
    exited = proc2.poll()
    os.killpg(os.getpgid(proc2.pid), signal.SIGTERM)  # whole process tree
    try:
        text = proc2.communicate(timeout=15)[0]
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc2.pid), signal.SIGKILL)
        text = proc2.communicate()[0]
    served = False
    try:
        httpx.get("http://127.0.0.1:8766/cash", timeout=2)
        served = True
    except Exception:  # noqa: BLE001
        pass
    check("The literal `uvicorn main --reload --port 8000` form does NOT start a server (uvicorn 0.54 needs main:app)", 'must be in format "<module>:<attribute>"' in text and not served, text.strip().splitlines()[-1][:110] if text.strip() else "no output")
    time.sleep(1)
    check("...and its process tree is cleaned up", subprocess.run(["lsof", "-nP", "-iTCP:8766", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.strip() == "")


if __name__ == "__main__":
    try:
        asyncio.run(main_tests())
        asyncio.run(p8_tests())
        asyncio.run(p8b_tests())
        asyncio.run(env_tests())
        real_server_tests()
    finally:
        pass
    real_after = snapshot(REAL_PROTECTED)
    check("REAL databases, mcp_smoke.json, audit_trail.json and desk_tickets.html are unchanged", real_after == real_before)
    real_db = sqlite3.connect(f"file:{REAL / 'data' / 'campus_customs_new.db'}?mode=ro&immutable=1", uri=True)
    check("REAL working database: no payments, cash still 3400", real_db.execute("select count(*) from payments").fetchone()[0] == 0 and real_db.execute("select balance from cash_accounts").fetchone()[0] == 3400.0)
    real_db.close()
    check("No __pycache__ left in the real project", not [p for p in REAL.rglob("__pycache__") if ".venv" not in p.parts])
    # ---- the throwaway token must not appear anywhere: test output, server logs, audit trails, files ----
    leaks = []
    for label, text in (("test output", "\n".join(PRINTED)), ("uvicorn output", "\n".join(SERVER_OUTPUT))):
        if TOKEN in text:
            leaks.append(label)
    for root in (CLONE, REAL):
        for f in root.rglob("*"):
            if f.is_file() and ".venv" not in f.parts and f.suffix in {".py", ".md", ".json", ".html", ".js", ".txt", ".db", ".tmp", ""} and f.stat().st_size < 5_000_000:
                try:
                    if TOKEN.encode() in f.read_bytes():
                        leaks.append(str(f.relative_to(root)))
                except OSError:
                    pass
    check("The admin token appears nowhere: not in test output, server logs, audit trails, code, docs, dashboard or databases", not leaks, str(leaks))
    shutil.rmtree(TMP, ignore_errors=True)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)
