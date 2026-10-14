"""TEST-ONLY scripted agent team for the dashboard and API tests. It makes NO model calls.

Every agent is a PydanticAI FunctionModel that follows a fixed script, but all shop facts still come
from the real MCP server (the scripted agents call the real read-only tools), so the audit trail,
delegation, usage counting and reports behave like a real run. Model names are ``stub-<agent>`` and
runs are marked ``run_mode: stub``. This file is copied into a disposable project copy for browser
tests; it is never used by the real ``backend/main.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import agents

PLAN: dict[int, list[tuple[str, str]]] = {  # ticket -> [(specialist, task Boss gives it)]
    101: [("inventory", "Check the stock position for the Bulldog tee in the requested size."),
          ("accounting", "Review the linked vendor invoice, payment records and cash position.")],
    102: [("facilities", "Verify the rent notice against the lease record."),
          ("accounting", "Report cash position and payment-record status for the rent.")],
    103: [("inventory", "Evaluate the 20-unit navy hoodie order against stock."),
          ("customer_service", "Draft a customer-safe reply that promises nothing.")],
}
TOOLS: dict[tuple[str, int], list[tuple[str, dict[str, Any]]]] = {
    ("inventory", 101): [("check_product_stock", {"product": "CC-TEE-WHITE", "size": "S", "quantity": 1})],
    ("accounting", 101): [("get_invoice_status", {"invoice_id": 501}), ("get_cash_position", {})],
    ("facilities", 102): [("get_rent_obligation", {"lease_id": 1})],
    ("accounting", 102): [("get_cash_position", {}), ("get_payment_history", {})],
    ("inventory", 103): [("evaluate_bulk_order", {"sku": "CC-HOOD-NAVY", "size": "M", "quantity": 20})],
    ("customer_service", 103): [("get_customer_safe_ticket_context", {"ticket_id": 103})],
}
ESCALATIONS: dict[tuple[str, int], tuple[str, str, str]] = {
    ("accounting", 101): ("payment", "Decide whether to pay vendor invoice 501.", "Review the invoice and decide; do not promise shipment."),
    ("facilities", 102): ("payment", "Decide whether to pay this month's rent.", "Verify against the lease, then approve if appropriate."),
    ("inventory", 103): ("stock_commitment", "Decide any partial fill or restock for the 20-unit order.", "A human decides; 12 units are short."),
}


def _facts(tool: str, content: Any) -> str:
    try:
        d = json.loads(content) if isinstance(content, str) else content
        if tool == "check_product_stock":
            r = d["products"][0]["requested_size"]
            return f"Size {r['size']}: {r['qty_on_hand']} on hand, {r['quantity_requested']} requested (shortfall {r['shortfall']})."
        if tool == "get_invoice_status":
            i = d["invoices"][0]
            return f"Invoice {i['invoice_id']}: ${i['amount']:,.2f} to {i['vendor_name']}, {i['days_past_due']} days past due, status {i['status']}."
        if tool == "get_cash_position":
            return f"Checking cash is ${d['total_cash']:,.2f}."
        if tool == "get_rent_obligation":
            l = d["leases"][0]
            return f"Lease {l['lease_id']}: {l['landlord']}, ${l['monthly_rent']:,.2f} due {l['next_due']}."
        if tool == "get_payment_history":
            return f"{d['count']} payments are recorded."
        if tool == "evaluate_bulk_order":
            return f"{d['qty_on_hand']} on hand for {d['shortfall'] + d['qty_on_hand']} requested (shortfall {d['shortfall']}); list price ${d['list_price']:,.0f}."
        if tool == "get_customer_safe_ticket_context":
            return f"Customer-safe view: list price ${d['product']['list_price']:,.0f}, requested size shows {d['product']['requested_size']['qty_on_hand']} on hand."
    except Exception:  # noqa: BLE001
        pass
    return f"{tool} returned data."


def _ticket(messages) -> int:
    for m in messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, UserPromptPart):
                h = re.search(r"[Tt]icket (\d+)", str(p.content))
                if h:
                    return int(h.group(1))
    return 0


def _brain(name: str, delay: float, fail_tickets: frozenset[int]):
    async def brain(messages, info: AgentInfo) -> ModelResponse:
        if delay:
            await asyncio.sleep(delay)
        n = sum(isinstance(m, ModelResponse) for m in messages)
        t = _ticket(messages)
        out = info.output_tools[0].name
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if name == "boss":
            if t in fail_tickets and n == 1:
                raise RuntimeError("scripted stub failure")
            if n == 0:
                return ModelResponse(parts=[ToolCallPart("get_ticket_context", {"ticket_id": t})])
            plan = PLAN.get(t, [])
            if n - 1 < len(plan):
                to, task = plan[n - 1]
                return ModelResponse(parts=[ToolCallPart("delegate", {"to": to, "task": f"Ticket {t}. {task}"})])
            reps = [json.loads(r.content) for r in returns if r.tool_name == "delegate" and str(r.content).startswith("{")]
            return ModelResponse(parts=[ToolCallPart(out, {
                "ticket_id": t, "status": "needs_human_approval",
                "summary": f"Scripted recommendation for ticket {t}: " + " ".join(r["summary"] for r in reps)[:300],
                "actions_recommended": ["Wait for a human decision. Nothing has been paid or promised."],
                "escalations": [e for r in reps for e in r.get("escalations", [])],
                "customer_draft": next((r.get("customer_draft") for r in reps if r.get("customer_draft")), None),
                "specialists_consulted": [r["agent"] for r in reps],
            })])
        plan = TOOLS.get((name, t), [])
        if n < len(plan):
            tool, args = plan[n]
            return ModelResponse(parts=[ToolCallPart(tool, args)])
        facts = [{"statement": _facts(r.tool_name, r.content), "source_tool": r.tool_name} for r in returns if r.tool_name != "delegate"]
        rep: dict[str, Any] = {"agent": name, "summary": " ".join(f["statement"] for f in facts[:2]) or "No facts needed.", "facts": facts}
        if (name, t) in ESCALATIONS:
            cat, need, rec = ESCALATIONS[(name, t)]
            rep["escalations"] = [{"category": cat, "decision_needed": need, "reason": "Needs human authority.", "recommendation": rec}]
        if name == "customer_service":
            rep["customer_draft"] = "Hello, thanks for reaching out. We are checking and a team member will follow up."
        return ModelResponse(parts=[ToolCallPart(out, rep)])
    return brain


def make_team_factory(delay: float = 0.0, fail_tickets: frozenset[int] | set[int] = frozenset()) -> Callable[[Any], dict[str, Any]]:
    ft = frozenset(fail_tickets)
    return lambda toolset: agents.build_team(toolset, model_for=lambda name: FunctionModel(_brain(name, delay, ft), model_name=f"stub-{name}"))
