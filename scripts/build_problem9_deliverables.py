"""Builds the Problem 9 deliverables from REAL evidence. Read-only on the database and the audit trail.

Inputs : data/campus_customs_new.db (read-only), output/audit_trail.json, evidence/*.json, evidence/*.png
Outputs: output/desk_tickets.html (Actual sections + Cash tab; Expected sections untouched),
         output/resolved_tickets.json, output/resolved_board.html
Makes no model calls, no payments and no database writes.
"""
from __future__ import annotations

import base64, collections, hashlib, html, json, re, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E = html.escape
IDS = {101: "ca7516af05d8", 102: "832a7ef51ea1", 103: "88cabc37a99b"}
money = lambda v: f"${v:,.2f}"
signed = lambda v: ("−" if v < 0 else "+" if v > 0 else "") + money(abs(v))

# ------------------------------------------------------------------ load evidence
db = sqlite3.connect(f"file:{ROOT / 'data' / 'campus_customs_new.db'}?mode=ro&immutable=1", uri=True)
q = lambda s: db.execute(s).fetchall()
audit = json.loads((ROOT / "output" / "audit_trail.json").read_text())
pre_reset = json.loads((ROOT / "evidence" / "audit_trail.pre-problem9.json").read_text())
payrep = json.loads((ROOT / "evidence" / "payment_verification_rent_2026-09.json").read_text())
ev = {t: json.loads((ROOT / "evidence" / f"ticket{t}_run.json").read_text()) for t in IDS}
shot = {t: json.loads((ROOT / "evidence" / f"screenshot_ticket{t}.json").read_text()) for t in IDS}
desk_html = (ROOT / "output" / "desk_tickets.html").read_text()

tickets = {r[0]: r for r in q("select id,type,subject,status,sku,size,qty,lease_id,invoice_id from tickets")}
payments = q("select id,kind,ref_id,amount,account,paid_at,approved_by from payments")
start_balance = 3400.0
cash_now = q("select balance from cash_accounts where name='checking'")[0][0]
inv501 = q("select amount,status from invoices where id=501")[0]
hood_qty = q("select qty from inventory where sku='CC-HOOD-NAVY' and size='M'")[0][0]
hood_price = q("select list_price,unit_cost from pricing where sku='CC-HOOD-NAVY'")[0]
reset_ids = [r["audit_id"] for r in audit if r["event"] in ("reset_attempt", "reset_committed")]
pay_ids = {r["event"]: r["audit_id"] for r in audit if r["event"] in ("payment_attempt", "payment_committed")}
assert len(audit) == 241 and audit[:107] == pre_reset[:107] and len(payments) == 1
assert cash_now == 1000.0 and inv501 == (840.0, "open") and hood_qty == 8 and hood_price == (58.0, 22.0)
assert payments[0] == (1, "rent", 1202609, 2400.0, "checking", "2026-08-31", "Authorized course operator")
assert all(tickets[t][3] == "resolved" for t in IDS)

# ------------------------------------------------------------------ per-ticket facts from the audit trail
def run_facts(t):
    rid = IDS[t]
    recs = [r for r in audit if r["run_id"] == rid]
    assert recs == ev[t]["audit_records"] and {r["run_mode"] for r in recs} == {"live"}
    st = ev[t]["run_status"]
    assert st["status"] == "finished" and st["resolved"] is True and {r["model"] for r in recs if r["model"]} == {"gpt-6-luna"}
    dels = [(r["agent"], r["details"]["to"], r["details"]["task"]) for r in recs if r["event"] == "delegation"]
    tools = collections.defaultdict(collections.Counter)
    for r in recs:
        if r["event"] == "tool_call" and r["details"]["tool"] not in ("delegate", "final_result"):
            tools[r["agent"]][r["details"]["tool"]] += 1
    reports = {x["agent"]: x for x in st["agent_reports"]}
    agents = [a for a in dict.fromkeys(["boss"] + [d[1] for d in dels]) ]
    return dict(rid=rid, recs=recs, st=st, dels=dels, tools={k: dict(v) for k, v in tools.items()}, reports=reports, agents=agents, decision=st["decision"], usage=st["usage"])

F = {t: run_facts(t) for t in IDS}

# Expected plans, parsed from the (unchanged) Expected sections of the page
def expected(t):
    panel = re.search(rf'<section id="panel-t{t}".*?(?=<section id="panel-t|<section id="panel-cash")', desk_html, re.S).group(0)
    exp = re.search(r'<section class="card expected".*?</section>', panel, re.S).group(0)
    path = re.findall(r'<tr data-from="(\w+)" data-to="(\w+)">', exp)
    tools = re.findall(r'<li data-tool="(\w+)" data-agent="(\w+)">', exp)
    return dict(path=path, tools=tools, first=re.search(r'data-first="(\w+)"', exp).group(1), section=exp)
X = {t: expected(t) for t in IDS}
exp_hash = {t: hashlib.sha256(X[t]["section"].encode()).hexdigest() for t in IDS}

def compare(t):
    f, x = F[t], X[t]
    ap = [(a, b) for a, b, _ in f["dels"]]
    exp_path = x["path"]
    missing = [p for p in exp_path if p not in ap]
    extra = [p for p in ap if p not in exp_path]
    act_tools = {tool for c in f["tools"].values() for tool in c}
    exp_tools = {tool for tool, _ in x["tools"]}
    by_agent_extra = {}
    for tool, agent in x["tools"]:
        pass
    return dict(expected_path=exp_path, actual_path=ap, missing_delegations=missing, extra_delegations=extra,
                first_specialist_expected=x["first"], first_specialist_actual=f["dels"][0][1],
                expected_tools=sorted(exp_tools), actual_tools=sorted(act_tools),
                expected_tools_not_used=sorted(exp_tools - act_tools), extra_tools=sorted(act_tools - exp_tools))
C = {t: compare(t) for t in IDS}

# ------------------------------------------------------------------ business facts (from the database)
def business(t):
    if t == 101:
        return dict(state="awaiting_human_decision", outstanding=[
            "Size S of the Classic Bulldog Tee is out of stock (0 on hand, 1 requested).",
            f"Vendor invoice 501 ({money(inv501[0])} to Bulldog Print Co) is still open and unpaid, 3 days past due at the business date.",
            "No restock, reprint authorization or customer fulfillment commitment was approved.",
            "Paying invoice 501 would not by itself put a tee on the shelf; the vendor's reprint lead time is 5 days."])
    if t == 102:
        return dict(state="paid", outstanding=[
            "Invoice 501 remains open (a competing obligation noted by Accounting); it was not part of this ticket's approval."])
    return dict(state="awaiting_human_decision", outstanding=[
        f"{hood_qty} size M hoodies on hand against 20 requested (shortfall 12).",
        f"List price {money(hood_price[0])}. The {money(hood_price[1])} unit cost is a cost fact and NOT an approved discount floor.",
        "No bulk discount, partial fulfillment, restock or customer commitment was approved. No purchase price exists in the shop's records."])
B = {t: business(t) for t in IDS}

OUTCOME = {
    101: "Workflow resolved. Size S is out of stock and invoice 501 ($840.00) remains open; no shipment, restock or payment was approved. No cash movement.",
    102: "Workflow resolved. The lease confirmed $2,400.00 due 2026-09-02. A human then approved one simulated rent payment (payment 1, $2,400.00, billing period 2026-09). Checking went from $3,400.00 to $1,000.00.",
    103: "Workflow resolved. 8 of 20 size M hoodies are on hand (shortfall 12) at $58.00 list. No discount, purchase, restock or customer commitment was approved. No cash movement.",
}
CASH = {101: 0.0, 102: -2400.0, 103: 0.0}
assert round(start_balance + sum(CASH.values()), 2) == cash_now == 1000.0
assert round(start_balance - sum(p[3] for p in payments), 2) == cash_now

# ------------------------------------------------------------------ resolved_tickets.json
def contribution(t, agent):
    r = F[t]["reports"].get(agent)
    return r["report"]["summary"] if r else F[t]["decision"]["summary"]

out_tickets = []
for t in IDS:
    f = F[t]; d = f["decision"]
    agents = []
    for a in f["agents"]:
        rep = f["reports"].get(a)
        agents.append(dict(agent=a, delegated_by=(rep["delegated_by"] if rep else None),
                           delegates_to=[b for x, b, _ in f["dels"] if x == a],
                           tools_used=f["tools"].get(a, {}), contribution=contribution(t, a)))
    approvals = []
    if t == 102:
        p = payments[0]
        approvals.append(dict(type="rent payment (simulated local ledger)", payment_id=p[0], kind=p[1], ref_id=p[2], lease_id=1, payee="Elm City Properties",
                              billing_period="2026-09", amount=p[3], account=p[4], paid_at=p[5], approved_by=p[6], approval_id=payrep["payment"]["approval_id"],
                              audit_ids=dict(payment_attempt=pay_ids["payment_attempt"], payment_committed=pay_ids["payment_committed"]),
                              note="Approved by a human after the agent run finished; the agents only recommended."))
    stmt = {101: "No human financial or fulfillment approval was granted. Invoice 501 was not paid, and no restock or customer commitment was approved.",
            102: "One human-approved simulated rent payment (payment 1). No other approval was granted.",
            103: "No human financial or fulfillment approval was granted: no discount, purchase, restock, partial fulfillment or customer commitment."}[t]
    out_tickets.append(dict(
        ticket_id=t, subject=tickets[t][2], type=tickets[t][1], final_status=tickets[t][3],
        workflow_resolved=True, business_state=B[t]["state"], outcome=OUTCOME[t],
        boss_recommendation=dict(status=d["status"], summary=d["summary"], escalations=[dict(category=e["category"], decision_needed=e["decision_needed"]) for e in d["escalations"]]),
        outstanding_business_decisions=B[t]["outstanding"],
        run=dict(run_id=f["rid"], run_mode="live", model="gpt-6-luna", status=f["st"]["status"], requests=f["usage"]["requests"], tool_calls=f["usage"]["tool_calls"],
                 input_tokens=f["usage"]["input_tokens"], output_tokens=f["usage"]["output_tokens"], audit_records=len(f["recs"])),
        agents=agents, delegations=[dict(from_agent=a, to_agent=b, task=task) for a, b, task in f["dels"]],
        expected_vs_actual=C[t], human_approvals=approvals, human_approval_statement=stmt,
        cash_change=dict(amount=CASH[t], type=("rent payment" if t == 102 else "none"), reason=(
            "Human-approved September 2026 rent payment to Elm City Properties." if t == 102 else
            "No payment, purchase or discount was approved for this ticket.")),
        screenshot=f"evidence/screenshot_ticket{t}.png"))
resolved = dict(
    schema_version="1.0", problem="MGT 409 Homework 5, Problem 9: Resolve the Tickets", business_date="2026-08-31",
    definitions=dict(final_status="tickets.status in the working database after a genuine live agent run finished normally ('resolved').",
                     workflow_resolved="The agent workflow finished. It does NOT mean the business issue is solved or that anything was paid.",
                     business_state="Derived from current records: paid | awaiting_human_decision | no_payment_applicable."),
    sources=["data/campus_customs_new.db (read-only)", "output/audit_trail.json", "evidence/ticket101_run.json", "evidence/ticket102_run.json", "evidence/ticket103_run.json", "evidence/payment_verification_rent_2026-09.json"],
    cash_reconciliation=dict(starting_balance=start_balance, ticket_changes={"101": 0.0, "102": -2400.0, "103": 0.0}, ending_balance=cash_now,
                             cash_accounts_checking=cash_now, payments_rows=len(payments), unpaid_invoice_501=dict(amount=inv501[0], status=inv501[1]),
                             arithmetic="3400.00 + 0.00 - 2400.00 + 0.00 = 1000.00"),
    audit=dict(records=len(audit), original_records=107, reset_records=2, run_records={"101": 47, "102": 36, "103": 47}, payment_records=2,
               reset_audit_ids=reset_ids, payment_audit_ids=pay_ids),
    tickets=out_tickets)
(ROOT / "output" / "resolved_tickets.json").write_text(json.dumps(resolved, indent=2, ensure_ascii=False) + "\n")

# ------------------------------------------------------------------ desk_tickets.html (Actual sections + Cash tab)
def actual_html(t):
    f, d, c = F[t], F[t]["decision"], C[t]
    pth = lambda ps: "; ".join(f"{a.replace('_',' ').title()} → {b.replace('_',' ').title()}" for a, b in ps) if ps else "none"
    rows = "".join(f"<tr><td><span class='agent'>{E(a.replace('_',' ').title())}</span></td><td class='arrow'>→</td><td><span class='agent'>{E(b.replace('_',' ').title())}</span></td><td>{E(task)}</td></tr>" for a, b, task in f["dels"])
    agent_rows = ""
    for a in f["agents"]:
        rep = f["reports"].get(a)
        tl = "".join(f"<code>{E(tool)}</code>{' ×'+str(n) if n>1 else ''} " for tool, n in f["tools"].get(a, {}).items()) or "<span class='muted'>none</span>"
        agent_rows += (f"<tr><td><span class='agent'>{E(a.replace('_',' ').title())}</span></td><td>{E((rep['delegated_by'].title() if rep else '—'))}</td><td>{tl}</td>"
                       f"<td>{E(contribution(t, a))}</td></tr>")
    esc = "".join(f"<li><strong>{E(e['decision_needed'])}</strong> <span class='muted'>({E(e['category'].replace('_',' '))})</span></li>" for e in d["escalations"])
    diff = []
    if c["missing_delegations"] or c["extra_delegations"]:
        diff.append(f"<li><strong>Delegation path differs.</strong> Expected: {E(', '.join(f'{a}→{b}' for a,b in c['expected_path']))}. Actual: {E(', '.join(f'{a}→{b}' for a,b in c['actual_path']))}."
                    + (f" Expected but did not happen: {E(', '.join(f'{a}→{b}' for a,b in c['missing_delegations']))}." if c["missing_delegations"] else "")
                    + (f" Happened but not expected: {E(', '.join(f'{a}→{b}' for a,b in c['extra_delegations']))}." if c["extra_delegations"] else "") + "</li>")
    else:
        diff.append("<li>Delegation path matched the expected plan.</li>")
    diff.append(f"<li>First specialist called: <strong>{E(c['first_specialist_actual'].replace('_',' '))}</strong> (expected {E(c['first_specialist_expected'].replace('_',' '))}) — {'match' if c['first_specialist_actual']==c['first_specialist_expected'] else 'different'}.</li>")
    diff.append(f"<li>Expected tools not used: {E(', '.join(c['expected_tools_not_used']) or 'none')}. Tools used that were not in the expected list: {E(', '.join(c['extra_tools']) or 'none')}.</li>")
    other = [a for a in ("inventory", "accounting", "facilities", "customer_service") if a not in f["agents"]]
    diff.append(f"<li>Specialists not involved: {E(', '.join(x.replace('_',' ') for x in other) or 'none')}.</li>")
    why = {101: "Boss called Accounting and Customer Service itself instead of Inventory handing off to Accounting, and Customer Service drafted a reply that the plan had deliberately left out (the plan held it back until a human decides). The draft promises nothing.",
           102: "Boss consulted Accounting directly (the Boss prompt's rule about checking cash before a significant payment) instead of Facilities handing the cash question to Accounting.",
           103: "Boss also consulted Accounting (list price, cost and cash facts) and called Customer Service directly, instead of Inventory handing off to Customer Service; Accounting was not in the plan."}[t]
    pay = ""
    if t == 102:
        p = payments[0]
        pay = (f"<div class='callout'><strong>Human approval after the run (not an agent action).</strong> The agents only recommended. Afterwards a human approved one <em>simulated</em> rent payment in the local ledger: "
               f"payment #{p[0]}, {money(p[3])} to Elm City Properties, lease 1, billing period 2026-09, approved by “{E(p[6])}”, dated {p[5]} (audit {pay_ids['payment_attempt']} / {pay_ids['payment_committed']}). "
               f"Checking: {money(start_balance)} → {money(cash_now)}.</div>")
    biz_label = {"awaiting_human_decision": "Needs a human decision", "paid": "Payment recorded"}[B[t]["state"]]
    outst = "".join(f"<li>{E(x)}</li>" for x in B[t]["outstanding"])
    u = f["usage"]
    draft = next((r["report"]["customer_draft"] for r in f["reports"].values() if r["report"].get("customer_draft")), None)
    return f"""
        <p class="runline"><span class="badge">Live run</span> run id <code>{f['rid']}</code> · model <code>gpt-6-luna</code> via Portkey · status <strong>finished</strong> · {len(f['recs'])} audit records · {u['requests']} model requests, {u['tool_calls']} tool calls, {u['input_tokens']+u['output_tokens']:,} tokens ({u['input_tokens']:,} in / {u['output_tokens']:,} out)</p>
        <div class="split">
          <div class="pill good"><strong>Workflow:</strong> resolved <span class="muted">(ticket status in the database; the agent run finished normally)</span></div>
          <div class="pill attn"><strong>Business:</strong> {E(biz_label)} <span class="muted">(derived from current records)</span></div>
        </div>
        <h4>Agents that worked, what they delegated, and tools used</h4>
        <p class="muted">Delegation path actually taken: {E(pth([(a,b) for a,b,_ in f['dels']]))}.</p>
        <table class="deleg"><thead><tr><th>Agent</th><th>Asked by</th><th>MCP tools used</th><th>Contribution (from its report)</th></tr></thead><tbody>{agent_rows}</tbody></table>
        <h4>Delegations (from the audit trail)</h4>
        <table class="deleg"><thead><tr><th>From</th><th></th><th>To</th><th>Task given</th></tr></thead><tbody>{rows}</tbody></table>
        <h4>Boss's recommendation</h4>
        <p>{E(d['summary'])}</p>
        <p class="muted"><strong>Waiting on a human:</strong></p><ul class="rec">{esc}</ul>
        {('<p class="muted">Customer-reply draft (a human reviews before anything is sent): <em>“'+E(draft)+'”</em></p>') if draft else ''}
        <h4>Still outstanding in the business</h4><ul class="rec">{outst}</ul>
        {pay}
        <h4>Expected versus actual</h4><ul class="rec">{''.join(diff)}<li><em>Why it differed:</em> {E(why)}</li></ul>
        """

new_html = desk_html
for t in IDS:
    old = re.search(rf'<div class="actual-body" data-actual="{t}"[^>]*></div>', new_html).group(0)
    new_html = new_html.replace(old, f'<div class="actual-body filled" data-actual="{t}" aria-label="Actual results for ticket {t}, from the genuine live run">{actual_html(t)}</div>')
new_html = new_html.replace("<small>(to be completed in Problem 9)</small>", "<small>(from the genuine live run, not a plan)</small>")
new_html = new_html.replace("<footer>Expected sections are plans only. Actual sections stay empty until Problem 9.</footer>", "<footer>Expected sections are the plans written before any run. Actual sections and the Cash tab come from the three genuine live runs, the audit trail and the working database. Workflow “resolved” means the agent run finished; it is not a business approval.</footer>")

rec_row = lambda t, change, typ, why: f"<tr><td>Ticket {t}</td><td class='num'>{signed(change) if change else '$0.00'}</td><td>{typ}</td><td>{why}</td></tr>"
p = payments[0]
cash_html = f"""<h2>Cash</h2>
    <p class="muted">Business date 2026-08-31. Every figure comes from the working database (<code>payments</code> and <code>cash_accounts</code>), not from agent text. All money here is the homework's <strong>simulated local ledger</strong>; no real transfer occurred.</p>
    <table class="deleg cashtab">
      <thead><tr><th>Line</th><th class="num">Amount</th><th>Type</th><th>Why</th></tr></thead>
      <tbody>
        <tr class="total"><td>Starting checking balance (after the database reset)</td><td class="num">{money(start_balance)}</td><td>—</td><td>Read from <code>cash_accounts</code> and <code>GET /cash</code> right after the reset (audit {E(', '.join(reset_ids))}); 0 payments recorded at that point.</td></tr>
        {rec_row(101, 0, 'none', 'Workflow resolved, but no payment was approved. Invoice 501 ($840.00) is still open and unpaid; paying it was not authorized and would not by itself fix the size S shortage.')}
        {rec_row(102, -2400.0, 'Rent payment (simulated)', f"Human-approved September 2026 rent to Elm City Properties, lease 1, billing period 2026-09. Payment #{p[0]}, approved by “{E(p[6])}”, dated {p[5]}. Audit ids {pay_ids['payment_attempt']} (attempt) and {pay_ids['payment_committed']} (committed).")}
        {rec_row(103, 0, 'none', 'No discount, purchase, restock or customer commitment was approved. The $22.00 unit cost is not an approved discount floor; 8 of 20 hoodies are on hand and no purchase price exists.')}
        <tr class="total"><td>Ending checking balance</td><td class="num">{money(cash_now)}</td><td>—</td><td>Matches <code>cash_accounts</code> in the working database.</td></tr>
      </tbody>
    </table>
    <div class="callout"><strong>Arithmetic:</strong> $3,400.00 + $0.00 (101) − $2,400.00 (102) + $0.00 (103) = <strong>$1,000.00</strong>.
      Check: starting balance − sum of <code>payments</code> rows ($2,400.00, exactly one row) = $1,000.00 = <code>cash_accounts.balance</code>.
      <code>GET /cash</code> returned $1,000.00 / as of 2026-08-31 when the payment was verified (<code>evidence/payment_verification_rent_2026-09.json</code>).</div>
    <p class="muted"><strong>Not a cash change:</strong> vendor invoice 501 ($840.00) is still <em>open and unpaid</em>. If a human later paid it, checking would fall to $160.00; that has not happened. No hoodie shortage was filled and no discount was authorized.</p>"""
new_html = re.sub(r'<h2>Cash</h2>\s*<p class="coming">Coming later\.</p>', lambda m: cash_html, new_html, count=1)
css = """
  .actual-body.filled { min-height: 0; border: 1px solid var(--line); background: var(--white); padding: 4px 2px; }
  .runline { margin: 0 0 10px; color: var(--muted); }
  .split { display: flex; flex-wrap: wrap; gap: 10px; margin: 8px 0 4px; }
  .pill { border-radius: 8px; padding: 6px 12px; border: 1px solid var(--line); background: var(--light-blue); color: var(--navy); }
  .pill.good { background: #e4f3ea; border-color: #b9dcc7; color: #1c6b43; } .pill.attn { background: #fff2d8; border-color: #f0d9a6; color: #8a4f00; }
  .callout { margin: 12px 0; padding: 10px 14px; border-left: 4px solid var(--blue); background: var(--pale-blue); border-radius: 6px; }
  .num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; } .total td { font-weight: 700; background: var(--pale-blue); }
  table.cashtab td, table.cashtab th { font-size: .95rem; }
"""
new_html = new_html.replace("</style>", css + "</style>", 1)
(ROOT / "output" / "desk_tickets.html").write_text(new_html)

# Expected sections must be byte-identical
chk = (ROOT / "output" / "desk_tickets.html").read_text()
for t in IDS:
    again = re.search(rf'<section id="panel-t{t}".*?(?=<section id="panel-t|<section id="panel-cash")', chk, re.S).group(0)
    assert hashlib.sha256(re.search(r'<section class="card expected".*?</section>', again, re.S).group(0).encode()).hexdigest() == exp_hash[t]

# ------------------------------------------------------------------ resolved_board.html (embedded genuine screenshots)
def b64(t): return base64.b64encode((ROOT / "evidence" / f"screenshot_ticket{t}.png").read_bytes()).decode()
figs = ""
for t in IDS:
    s = shot[t]; f = F[t]
    figs += f"""
  <section id="t{t}" class="ticket">
    <h2>Ticket {t}: {E(tickets[t][2])}</h2>
    <p class="meta"><span class="chip good">✓ Workflow resolved</span> <span class="chip {'good' if B[t]['state']=='paid' else 'attn'}">{'Rent payment recorded' if t==102 else 'Needs a human decision'}</span>
      Live run <code>{f['rid']}</code> · captured {E(s['capturedAt'])} · {E(s['viewport'])} · from the React dashboard connected to the real backend</p>
    <p>{E(OUTCOME[t])}</p>
    <ul>{''.join(f'<li>{E(x)}</li>' for x in B[t]['outstanding'])}</ul>
    <figure><img alt="Screenshot of the React dashboard showing Ticket {t} resolved, its agent activity and summary" src="data:image/png;base64,{b64(t)}"><figcaption>Genuine screenshot: <code>evidence/screenshot_ticket{t}.png</code> (SHA-256 {hashlib.sha256((ROOT/'evidence'/f'screenshot_ticket{t}.png').read_bytes()).hexdigest()[:16]}…)</figcaption></figure>
  </section>"""
board = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Customs: Resolved Board (Tickets 101, 102, 103)</title>
<style>
  :root {{ --navy:#0b2a55; --blue:#1e6bb8; --light:#e6f0fb; --pale:#f4f8fd; --line:#c9dcf0; --ink:#12233f; --muted:#4b5f7a; }}
  body {{ margin:0; background:var(--pale); color:var(--ink); font:16px/1.55 "Avenir Next","Segoe UI",system-ui,Helvetica,Arial,sans-serif; }}
  header {{ background:linear-gradient(120deg,#0b2a55,#14396f 60%,#1d4f93); color:#fff; padding:22px 28px; }}
  header h1 {{ margin:0; font:600 1.7rem Georgia,serif; }} header p {{ margin:.3rem 0 0; color:#cfe1f6; }}
  main {{ max-width:1180px; margin:0 auto; padding:22px 24px 48px; }}
  nav {{ display:flex; gap:10px; flex-wrap:wrap; margin:0 0 16px; }} nav a {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:6px 14px; color:var(--navy); font-weight:600; text-decoration:none; }}
  .note {{ background:#fff; border:1px solid var(--line); border-left:5px solid var(--blue); border-radius:8px; padding:12px 16px; margin-bottom:20px; }}
  .ticket {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:18px 22px; margin-bottom:26px; }}
  h2 {{ margin:0 0 6px; color:var(--navy); font:600 1.35rem Georgia,serif; }} .meta {{ color:var(--muted); font-size:.9rem; }}
  .chip {{ display:inline-block; border-radius:999px; padding:1px 11px; font-weight:700; font-size:.8rem; border:1px solid var(--line); }} .chip.good {{ background:#e4f3ea; color:#1c6b43; border-color:#b9dcc7; }} .chip.attn {{ background:#fff2d8; color:#8a4f00; border-color:#f0d9a6; }}
  code {{ background:var(--light); border:1px solid var(--line); border-radius:5px; padding:0 5px; font-size:.88em; }}
  figure {{ margin:14px 0 0; }} img {{ display:block; width:100%; height:auto; border:1px solid var(--line); border-radius:8px; }} figcaption {{ color:var(--muted); font-size:.85rem; margin-top:6px; }}
</style></head><body>
<header><h1>Resolved Board</h1><p>Genuine screenshots of the React dashboard for Tickets 101, 102 and 103 after their live agent runs (business date 2026-08-31). This page works offline.</p></header>
<main>
  <nav><a href="#t101">Ticket 101</a><a href="#t102">Ticket 102</a><a href="#t103">Ticket 103</a></nav>
  <div class="note"><strong>“Resolved” means the agent workflow finished.</strong> It does not mean the business problem is solved. Ticket 101's vendor invoice ($840.00) is still unpaid and its shirt is still out of stock; Ticket 103 still needs a human decision on any discount and on the 12-unit shortage; only Ticket 102 involved a human-approved payment (one simulated rent payment of $2,400.00, taking checking from $3,400.00 to $1,000.00).</div>
  {figs}
</main></body></html>
"""
(ROOT / "output" / "resolved_board.html").write_text(board)
print("built: desk_tickets.html, resolved_tickets.json, resolved_board.html")
