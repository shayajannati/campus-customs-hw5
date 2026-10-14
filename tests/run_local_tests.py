"""Local tests for Problem 5. No live model calls: agents run on scripted stub models
(PydanticAI FunctionModel), but every shop fact is fetched through the REAL MCP server.

Run:  .venv/bin/python -B tests/run_local_tests.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

import agents  # noqa: E402
from audit import AuditCorruptError, AuditTrail  # noqa: E402
from models import AGENT_NAMES, RunLimits  # noqa: E402
from orchestrator import process_ticket  # noqa: E402

DBS = [ROOT / "data" / "campus_customs.db", ROOT / "data" / "campus_customs_new.db"]


def _requester_pattern() -> str:
    """Regex for the ticket requester's real name, read from the course database (never hard-coded or printed)."""
    import sqlite3

    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'campus_customs.db'}?mode=ro&immutable=1", uri=True)
    try:
        name = conn.execute("select requester from tickets where id = 101").fetchone()[0]
    finally:
        conn.close()
    return "|".join(re.escape(w) for w in name.split())


SENSITIVE_RX = _requester_pattern()
REAL_AUDIT = ROOT / "output" / "audit_trail.json"
PROTECTED = DBS + [ROOT / "output" / "mcp_smoke.json", REAL_AUDIT]
# SHA-256 of the 88 stub-run records that were in the audit trail before live testing began.
LEGACY_COUNT = 88
LEGACY_SHA = "6e342ed9a71716f09663a0096e0f80cabda3f9ab95fb9cc41a1c2966ea1816fe"


def canon_sha(records) -> str:
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stub_runs(records) -> set[str]:
    """run_ids that are stub runs: run_mode == "stub", or (legacy records, which have no
    run_mode) any record in the run whose model name starts with "stub-"."""
    return {r["run_id"] for r in records if r.get("run_mode") == "stub" or str(r.get("model") or "").startswith("stub-")}
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def sha(paths) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


# --------------------------------------------------------------------------- #
# Scripted stub brains
# --------------------------------------------------------------------------- #
PLAN = {  # which specialists the stub Boss delegates to, per ticket
    101: ["inventory", "accounting", "customer_service"],
    102: ["facilities", "accounting"],
    103: ["inventory", "accounting", "customer_service"],
}
TOOLS = {  # (agent, ticket) -> MCP tool calls the stub specialist makes
    ("inventory", 101): [("check_product_stock", {"product": "CC-TEE-WHITE", "size": "S", "quantity": 1})],
    ("accounting", 101): [("get_invoice_status", {"invoice_id": 501}), ("get_cash_position", {})],
    ("customer_service", 101): [("check_product_stock", {"product": "CC-TEE-WHITE", "size": "S"})],
    ("facilities", 102): [("get_rent_obligation", {"lease_id": 1})],
    ("accounting", 102): [("get_cash_position", {}), ("get_payment_history", {})],
    ("inventory", 103): [("evaluate_bulk_order", {"sku": "CC-HOOD-NAVY", "size": "M", "quantity": 20})],
    ("accounting", 103): [("evaluate_bulk_order", {"sku": "CC-HOOD-NAVY", "size": "M", "quantity": 20})],
    ("customer_service", 103): [("check_product_stock", {"product": "CC-HOOD-NAVY", "size": "M", "quantity": 20})],
}
ESC = {
    ("accounting", 101): ("payment", "Pay overdue invoice 501?"),
    ("facilities", 102): ("payment", "Pay rent to the landlord?"),
    ("accounting", 103): ("discount", "Approve any bulk discount?"),
    ("inventory", 103): ("stock_commitment", "Commit partial stock or reprint?"),
}
SEEN_TOOLS: dict[str, set[str]] = {}


def _ticket(messages) -> int:
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, UserPromptPart):
                hit = re.search(r"[Tt]icket (\d+)", str(p.content))
                if hit:
                    return int(hit.group(1))
    return 0


def _responses(messages) -> int:
    return sum(isinstance(m, ModelResponse) for m in messages)


def _returns(messages) -> list[ToolReturnPart]:
    return [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]


def _final(info: AgentInfo, payload: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])


def make_brain(name: str, hook=None):
    def brain(messages, info: AgentInfo) -> ModelResponse:
        SEEN_TOOLS.setdefault(name, set()).update(t.name for t in info.function_tools)
        t, n = _ticket(messages), _responses(messages)
        if hook:
            out = hook(name, t, n, messages, info)
            if out is not None:
                return out
        if name == "boss":
            plan = PLAN.get(t, [])
            if n < len(plan):
                return ModelResponse(parts=[ToolCallPart("delegate", {"to": plan[n], "task": f"Help with ticket {t}."})])
            reports = [json.loads(r.content) for r in _returns(messages) if r.tool_name == "delegate" and str(r.content).startswith("{")]
            return _final(info, {
                "ticket_id": t, "status": "needs_human_approval",
                "summary": f"Stub recommendation for ticket {t} from {len(reports)} reports.",
                "actions_recommended": ["Wait for human approval."],
                "escalations": [e for r in reports for e in r.get("escalations", [])],
                "customer_draft": next((r.get("customer_draft") for r in reports if r.get("customer_draft")), None),
                "specialists_consulted": [r["agent"] for r in reports],
            })
        plan = TOOLS.get((name, t), [])
        if n < len(plan):
            tool, args = plan[n]
            return ModelResponse(parts=[ToolCallPart(tool, args)])
        rets = [r for r in _returns(messages) if r.tool_name != "delegate"]
        rep = {
            "agent": name, "summary": f"Stub {name} report for ticket {t}.",
            "facts": [{"statement": str(r.content)[:150], "source_tool": r.tool_name} for r in rets],
        }
        if (name, t) in ESC:
            cat, q = ESC[(name, t)]
            rep["escalations"] = [{"category": cat, "decision_needed": q, "reason": "Needs human authority.", "recommendation": "Review and decide."}]
        if name == "customer_service":
            rep["customer_draft"] = "Hello, thanks for reaching out. We are checking and a team member will follow up."
        return _final(info, rep)
    return brain


def stub_models(hook=None):
    return lambda name: FunctionModel(make_brain(name, hook), model_name=f"stub-{name}")


async def run_ticket(ticket_id, audit_path, hook=None, limits=None):
    toolset = agents.build_mcp_toolset()
    team = agents.build_team(toolset, model_for=stub_models(hook), limits=limits)
    async with toolset:
        return await process_ticket(ticket_id, team, AuditTrail(audit_path), limits, run_mode="stub")


def tmp_audit() -> Path:
    return Path(tempfile.mkdtemp()) / "audit_trail.json"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
async def main() -> None:
    before = sha(PROTECTED)

    # -- R1/R3: five agents with distinct, detailed prompt files ------------------
    prompts = {n: (ROOT / "backend" / "prompts" / f"{n}.md") for n in AGENT_NAMES}
    check("R3 one prompt file per agent in backend/prompts/", all(p.is_file() for p in prompts.values()) and len(list((ROOT / "backend/prompts").glob("*"))) == 5)
    check("R6 prompts are detailed (>=500 words each) and distinct", all(len(p.read_text().split()) >= 500 for p in prompts.values()) and len({p.read_text() for p in prompts.values()}) == 5)

    # -- R5: gpt-6-luna and Portkey for every agent (construction only, no network)
    os.environ["PORTKEY_API_KEY"] = "dummy-key-for-construction-only"
    toolset = agents.build_mcp_toolset()
    real_team = agents.build_team(toolset)
    names = {n: getattr(a.model, "model_name", None) for n, a in real_team.items()}
    check("R5 all five agents use exactly gpt-6-luna", set(names.values()) == {"gpt-6-luna"} and len(names) == 5, str(names))
    check("R5 base URL is Portkey and key is read from PORTKEY_API_KEY", agents.PORTKEY_BASE_URL == "https://api.portkey.ai/v1" and "PORTKEY_API_KEY" in Path(agents.__file__).read_text())
    check("R2 every agent has a delegate tool to reach the others", all("delegate" in a._function_toolset.tools for a in real_team.values()))

    # -- MCP: 10 tools, all read-only; original three still work -----------------
    async with toolset:
        tools = await toolset.client.list_tools()
        tnames = sorted(t.name for t in tools)
        check("R7 server exposes the 3 original + 9 new read-only tools (12)", len(tnames) == 12 and {"check_product_stock", "get_rent_obligation", "evaluate_bulk_order"} <= set(tnames), str(tnames))
        check("R5b every MCP tool is annotated read-only", all((t.annotations and t.annotations.readOnlyHint) for t in tools))
        check("No tool name suggests paying/approving/writing", not any(re.search(r"pay_|approve|write|update|delete|insert|send|reserve|transfer", n) for n in tnames))
        r1 = (await toolset.client.call_tool("check_product_stock", {"product": "Bulldog Tee", "size": "S", "quantity": 1})).data
        r2 = (await toolset.client.call_tool("get_rent_obligation", {})).data
        r3 = (await toolset.client.call_tool("evaluate_bulk_order", {"sku": "CC-HOOD-NAVY", "size": "M", "quantity": 20})).data
        p1 = r1["products"][0]
        check("Original tool 1 still correct (tee S: 0, $28)", p1["list_price"] == 28 and p1["requested_size"]["qty_on_hand"] == 0)
        l = r2["leases"][0]
        check("Original tool 2 still correct (Elm City, 2400, 2026-09-02)", (l["landlord"], l["monthly_rent"], l["next_due"]) == ("Elm City Properties", 2400, "2026-09-02"))
        check("Original tool 3 still correct (hoodie M: 8/12/58/22)", (r3["qty_on_hand"], r3["shortfall"], r3["list_price"], r3["unit_cost"]) == (8, 12, 58, 22))
        inv = (await toolset.client.call_tool("get_invoice_status", {"invoice_id": 501})).data["invoices"][0]
        check("New tool: invoice 501 is overdue by 3 days at business date", inv["overdue"] and inv["days_past_due"] == 3 and inv["amount"] == 840)
        tk = (await toolset.client.call_tool("list_open_tickets", {})).data
        check("New tool: open tickets withhold requester names", tk["count"] == 3 and "requester" not in json.dumps(tk).lower().replace("requester_names_withheld", ""))
        cash = (await toolset.client.call_tool("get_cash_position", {})).data
        check("New tool: cash 3400, 160 left after listed obligations", cash["total_cash"] == 3400 and cash["cash_after_all_listed_obligations"] == 160)
        bd = (await toolset.client.call_tool("get_business_date", {})).data
        check("Business date comes from the desk table", bd["business_date"] == "2026-08-31")

    # -- Full stub runs on the three tickets (real MCP, scripted models) ----------
    path = tmp_audit()
    decisions = {}
    for tid in (101, 102, 103):
        decisions[tid] = await run_ticket(tid, path)
    trail = json.loads(path.read_text())
    check("R9 audit trail is valid JSON with records from all 3 runs", isinstance(trail, list) and len({r["run_id"] for r in trail}) == 3, f"{len(trail)} records")
    events = {r["event"] for r in trail}
    check("R9 audit covers prompts, tool calls, tool returns, delegation, final output", {"run_started", "user_prompt", "tool_call", "tool_return", "delegation", "final_output"} <= events, str(sorted(events)))
    check("R9 audit steps are ordered within each run", all([r["step"] for r in trail if r["run_id"] == rid] == sorted(r["step"] for r in trail if r["run_id"] == rid) for rid in {r["run_id"] for r in trail}))
    check("R9 audit records agent, parent, depth, usage", all({"agent", "parent_agent", "depth", "usage"} <= set(r) for r in trail) and any(r["parent_agent"] == "boss" for r in trail))
    mcp_calls = {r["details"]["tool"] for r in trail if r["event"] == "tool_call"}
    check("R8 agents fetched facts via MCP tools", {"check_product_stock", "get_invoice_status", "get_cash_position", "get_payment_history", "get_rent_obligation", "evaluate_bulk_order"} <= mcp_calls, str(sorted(mcp_calls)))
    d101, d102, d103 = decisions[101], decisions[102], decisions[103]
    check("Ticket 101: needs human approval, payment escalated, draft makes no shipment promise", d101.status == "needs_human_approval" and any(e.category == "payment" for e in d101.escalations) and "ship" not in (d101.customer_draft or "").lower())
    check("Ticket 102: payment escalated, nothing paid", d102.status == "needs_human_approval" and any(e.category == "payment" for e in d102.escalations))
    check("Ticket 103: discount and stock commitment escalated", {e.category for e in d103.escalations} >= {"discount", "stock_commitment"})
    blob = path.read_text()
    check("Audit has no keys, tokens, emails or requester names", not re.search(r"dummy-key|sk-[A-Za-z0-9]{12}|@[a-z]+\.[a-z]+|" + SENSITIVE_RX, blob))

    # -- Role least-privilege (what each model could see) -------------------------
    check("Inventory cannot see payment tools; Facilities cannot see stock tools", "get_payment_history" not in SEEN_TOOLS["inventory"] and "check_product_stock" not in SEEN_TOOLS["facilities"] and "get_cash_position" in SEEN_TOOLS["accounting"], str({k: sorted(v) for k, v in SEEN_TOOLS.items()}))

    # -- Delegation guards ----------------------------------------------------------
    def loop_hook(name, t, n, messages, info):
        if name == "inventory" and n == 0:
            return ModelResponse(parts=[ToolCallPart("delegate", {"to": "boss", "task": "loop back"})])
        if name == "inventory" and n == 1:
            return ModelResponse(parts=[ToolCallPart("delegate", {"to": "inventory", "task": "self"})])
    p2 = tmp_audit()
    await run_ticket(101, p2, loop_hook)
    refused = [r for r in json.loads(p2.read_text()) if r["event"] == "delegation_refused"]
    check("R8 delegation back to boss / to self is refused (no loops)", len(refused) == 2 and all("chain" in r["details"]["reason"] for r in refused))

    def deep_hook(name, t, n, messages, info):
        if name == "inventory" and n == 0:
            return ModelResponse(parts=[ToolCallPart("delegate", {"to": "accounting", "task": "go deeper"})])
        if name == "accounting" and n == 0:
            return ModelResponse(parts=[ToolCallPart("delegate", {"to": "facilities", "task": "even deeper"})])
    p3 = tmp_audit()
    await run_ticket(101, p3, deep_hook)
    rr = [r for r in json.loads(p3.read_text()) if r["event"] == "delegation_refused"]
    check("R8 chain longer than 3 agents is refused", any("chain length" in r["details"]["reason"] for r in rr))

    p4 = tmp_audit()
    cap = await run_ticket(101, p4, limits=RunLimits(max_delegations=1))
    rr = [r for r in json.loads(p4.read_text()) if r["event"] == "delegation_refused"]
    check("R8 per-ticket delegation cap enforced", any("maximum delegations" in r["details"]["reason"] for r in rr))

    p5 = tmp_audit()
    lim = await run_ticket(101, p5, limits=RunLimits(request_limit=2))
    err = [r for r in json.loads(p5.read_text()) if r["event"] == "error"]
    check("Token/request limit stops the run safely and is audited", lim.status == "blocked" and err and err[0]["details"]["type"] == "UsageLimitExceeded")

    p6 = tmp_audit()
    lim2 = await run_ticket(101, p6, limits=RunLimits(total_tokens_limit=1))
    check("Total-token limit also stops the run", lim2.status == "blocked")

    # -- Audit durability -----------------------------------------------------------
    p7 = tmp_audit()
    await run_ticket(103, p7)
    first = p7.read_bytes()
    await run_ticket(103, p7)
    second = p7.read_bytes()
    prefix = first.rstrip()[:-1].rstrip()
    check("R9 second run appends; earlier bytes are untouched and file stays valid", second.startswith(prefix) and len(second) > len(first) and isinstance(json.loads(second), list))
    p8 = tmp_audit()
    p8.write_text('[ {"not": "finished" ')
    snapshot = p8.read_bytes()
    try:
        AuditTrail(p8).append({"x": 1})
        raised = False
    except AuditCorruptError:
        raised = True
    check("Corrupt audit file: append refused and original preserved byte-for-byte", raised and p8.read_bytes() == snapshot and len(list(p8.parent.iterdir())) == 1)
    outcome = None
    try:
        await run_ticket(101, p8)
    except AuditCorruptError:
        outcome = "stopped"
    check("Corrupt audit file stops the agent run (fails closed)", outcome == "stopped" and p8.read_bytes() == snapshot)

    # -- Safety review: Customer Service cannot reach unit cost (server-enforced) ----
    check("CS toolset excludes get_ticket_context; has only customer-safe tools", agents.ROLE_TOOLS["customer_service"] == {"get_business_date", "get_customer_safe_ticket_context", "check_product_stock"} and "get_ticket_context" not in SEEN_TOOLS["customer_service"], str(sorted(SEEN_TOOLS["customer_service"])))
    async with agents.build_mcp_toolset() as ts:
        safe = {t: (await ts.client.call_tool("get_customer_safe_ticket_context", {"ticket_id": t})).data for t in (101, 102, 103)}
        stock = [(await ts.client.call_tool("check_product_stock", a)).data for a in ({"product": "CC-TEE-WHITE", "size": "S"}, {"product": "CC-HOOD-NAVY", "size": "M", "quantity": 20})]
        full = (await ts.client.call_tool("get_ticket_context", {"ticket_id": 103})).data
        bulk = (await ts.client.call_tool("evaluate_bulk_order", {"sku": "CC-HOOD-NAVY", "size": "M", "quantity": 20})).data
        inv = (await ts.client.call_tool("get_invoice_status", {"invoice_id": 501})).data
    # The only mention of these words is the "withheld" notice that says they are NOT provided.
    scanned = [{k: v for k, v in d.items() if k != "withheld"} for d in safe.values()]
    cs_blob = json.dumps([scanned, stock]).lower()
    check("CS-visible tool outputs contain no unit cost, margin, invoice, vendor, lease, cash or requester", not re.search(r"unit_cost|margin|max_discount|invoice|vendor|lease|monthly_rent|elm city|bulldog print|cash|balance|requester|" + SENSITIVE_RX.lower() + "|\\b840\\b|\\b2400\\b|\\b3400\\b", cs_blob), "scanned 3 safe contexts + 2 stock results; ticket 102's own subject says 'rent due', which is ticket text")
    check("Safe context still gives CS what it needs (list price, stock, shortfall)", safe[103]["product"]["list_price"] == 58 and safe[103]["product"]["requested_size"]["shortfall"] == 12 and safe[101]["product"]["requested_size"]["qty_on_hand"] == 0)
    check("Inventory/Accounting keep full pricing (unit cost 22) via evaluate_bulk_order and get_ticket_context", bulk["unit_cost"] == 22 and full["linked"]["pricing"]["unit_cost"] == 22 and "evaluate_bulk_order" in agents.ROLE_TOOLS["accounting"] and "evaluate_bulk_order" in agents.ROLE_TOOLS["inventory"])
    check("Invoice tool flags payment matching as unverified", inv["payment_matching_verified"] is False and len(inv["unverified_assumptions"]) == 3 and inv["payments_rows_in_database"] == 0)

    # -- Usage limits: conservative, and shared by every agent on a ticket ------------
    L = RunLimits()
    check("Limits are conservative: 16 requests, 20 tool calls, 60k tokens, chain 3, 4 delegations", (L.request_limit, L.tool_calls_limit, L.total_tokens_limit, L.max_chain_length, L.max_delegations) == (16, 20, 60_000, 3, 4))
    check("Per-request output cap is applied to every agent", all(a.model_settings and a.model_settings.get("max_tokens") == L.max_output_tokens_per_request for a in real_team.values()))
    peak = {}
    for r in json.loads(path.read_text()):
        if r["agent"] == "boss" and r["usage"]:
            for k, v in r["usage"].items():
                peak[(r["ticket_id"], k)] = max(peak.get((r["ticket_id"], k), 0), v)
    check("Meaningful delegation fits inside the limits (all 3 tickets, with headroom)", all(peak[(t, "requests")] <= L.request_limit * 0.75 and peak[(t, "tool_calls")] <= L.tool_calls_limit * 0.5 for t in (101, 102, 103)), str({k: v for k, v in peak.items() if k[1] in ("requests", "tool_calls")}))
    p9 = tmp_audit()
    shared = await run_ticket(101, p9, limits=RunLimits(request_limit=6))
    rec9 = json.loads(p9.read_text())
    boss_alone = max(r["usage"].get("requests", 0) for r in rec9 if r["agent"] == "boss")
    err9 = [r for r in rec9 if r["event"] == "error"]
    check("Limit is shared across agents: tripped inside a delegated agent, not in Boss alone", shared.status == "blocked" and any(r["agent"] != "boss" for r in err9) and boss_alone < 6, f"boss alone peaked at {boss_alone} of 6")
    p10 = tmp_audit()
    tc = await run_ticket(101, p10, limits=RunLimits(tool_calls_limit=2))
    check("Tool-call limit is shared and enforced", tc.status == "blocked")

    # -- Audit: 88 legacy stub records preserved; stub vs live distinguishable --------
    real_records = json.loads(REAL_AUDIT.read_text())
    check("All 88 original stub records are intact in output/audit_trail.json (content hash)", len(real_records) >= LEGACY_COUNT and canon_sha(real_records[:LEGACY_COUNT]) == LEGACY_SHA)
    check("Every legacy record belongs to a run identifiable as stub (stub-* model name, by run_id)", all(r["run_id"] in stub_runs(real_records[:LEGACY_COUNT]) for r in real_records[:LEGACY_COUNT]))
    copy = tmp_audit()
    copy.write_bytes(REAL_AUDIT.read_bytes())
    raw_before = copy.read_bytes()
    await run_ticket(102, copy)
    raw_after = copy.read_bytes()
    after = json.loads(raw_after)
    new = after[len(json.loads(raw_before)):]  # only what this stub run added (the real trail now also holds the live run)
    check("Future run appends to a copy of the real trail: original bytes untouched, valid JSON", raw_after.startswith(raw_before.rstrip()[:-1].rstrip()) and canon_sha(after[:LEGACY_COUNT]) == LEGACY_SHA and len(new) > 0)
    check("New records carry run_mode='stub' (live runs will carry 'live')", all(r.get("run_mode") == "stub" for r in new) and all(str(r["model"] or "stub-").startswith("stub-") for r in new) and any(r.get("run_mode") == "stub" for r in new))
    import inspect
    check("process_ticket defaults to run_mode='live'", inspect.signature(process_ticket).parameters["run_mode"].default == "live")

    # -- No bypass of MCP, no writes, no payments -----------------------------------
    backend_src = "\n".join(p.read_text() for p in (ROOT / "backend").glob("*.py"))
    check("R8 backend/ has no sqlite3 import and never names a .db file", "import sqlite3" not in backend_src and "sqlite3." not in backend_src and ".db" not in backend_src)
    server_src = (ROOT / "mcp_server" / "queries.py").read_text() + (ROOT / "mcp_server" / "server.py").read_text()
    check("MCP server has no INSERT/UPDATE/DELETE/DROP/ALTER/CREATE SQL", not re.search(r"(?i)\b(insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+table|alter\s+table|create\s+table)\b", server_src))
    sys.path.insert(0, str(ROOT / "mcp_server"))
    import queries  # noqa: WPS433
    try:
        queries._connect().execute("CREATE TABLE zz(a)")
        wrote = True
    except Exception:  # noqa: BLE001
        wrote = False
    check("Database connection rejects writes", not wrote)
    check("No payments recorded and databases/evidence unchanged by all tests", sha(PROTECTED) == before)
    pay = json.loads(json.dumps(queries.get_payment_history()))
    check("payments table still empty", pay["count"] == 0)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
