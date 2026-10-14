"""Read-only validation of the Problem 10 Reflection tab. No model calls, payments, resets or database writes.
Run:  .venv/bin/python -B tests/validate_problem10.py
"""
import hashlib, json, re, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = []
def ck(name, cond, detail=""):
    R.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

desk = (ROOT/"output"/"desk_tickets.html").read_text()
old = (ROOT/"evidence"/"desk_tickets.pre-problem10.html").read_text()
rt = json.loads((ROOT/"output"/"resolved_tickets.json").read_text())
panel = lambda h, pid: re.search(rf'<section id="panel-{pid}".*?(?=<section id="panel-|</main>)', h, re.S).group(0)
refl_html = panel(desk, "reflection")
refl = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", refl_html))
import html as _h; refl = _h.unescape(refl)

# structure
ck("Reflection tab no longer says 'Coming later'", "Coming later" not in refl_html)
heads = re.findall(r"<h3>(.*?)</h3>", refl_html)
ck("All five assignment questions have their own heading, in order", [h.split(":")[0] for h in heads[:5]] == [f"Question {i}" for i in range(1,6)], str(heads[:5]))
ck("Opening says 'resolved' means the agent workflow completed, not that the business issue was solved", re.search(r'class="lead".*?resolved.*?agent workflow completed.*?not mean the underlying business issue was solved', refl_html, re.S) is not None)
ck("Discusses read-only tools and protected human approval", "read-only tools and protected human approval" in refl and "no agent role has a write tool" in refl.lower())
ck("Q3 separates redundant retrieval from roles, mentions shared evidence / caching, and makes no speed claim", all(s in refl for s in ["retrieval", "shared evidence", "per-run cache", "cannot claim any version would be faster or cheaper"]) and not re.search(r"(?i)a single agent would (definitely )?be (much )?faster", refl))
ck("Q5 Purchasing agent is read-only and drafts only; purchase authorization and inventory changes are separate protected human-approved operations", all(s in refl for s in ["Purchasing agent", "read-only access", "draft", "protected backend operations that a human approves"]))
ck("Q1 covers all three tickets, with the Ticket 103 draft criticism framed as a tone risk, not a confirmed error", all(f"Ticket {t}" in refl for t in (101,102,103)) and "tone risk to edit, not a confirmed error" in refl)
ck("Hub discussion covers accountability, coordination and efficiency", all(s in refl for s in ["Accountability was clearer", "Coordination.", "Efficiency."]))

# three ticket comparisons accurate (against resolved_tickets.json, built from the audit trail)
ok_cmp = True; detail = []
for t in rt["tickets"]:
    ev = t["expected_vs_actual"]
    row = re.search(rf'<tr data-ticket="{t["ticket_id"]}">(.*?)</tr>', refl_html, re.S).group(1)
    txt = _h.unescape(re.sub(r"<[^>]+>", " ", row)); txt = re.sub(r"\s+", " ", txt)
    exp_chain = " → ".join(dict.fromkeys([ev["expected_path"][0][0]] + [b for _, b in ev["expected_path"]]))
    exp_ok = all(x.title().replace("_"," ") in txt for p in ev["expected_path"] for x in p)
    act_ok = all(f"{a.title().replace('_',' ')} → {b.title().replace('_',' ')}" in txt for a, b in ev["actual_path"])
    extras_ok = all(x in txt for x in ev["extra_tools"])
    none_missing = ev["expected_tools_not_used"] == [] and True
    miss_extra_claim = (("none missing and none extra" in txt) == (ev["extra_tools"] == []))
    delegations_actual = {tuple(p) for p in ev["actual_path"]}
    ok = exp_ok and act_ok and extras_ok and none_missing and miss_extra_claim
    ok_cmp &= ok; detail.append((t["ticket_id"], ok))
ck("Expected-vs-Actual rows match the audit-based evidence for tickets 101, 102 and 103 (paths, extra tools, none missing)", ok_cmp, str(detail))
ck("Ticket 101 and 103 rows say Customer Service/Accounting facts correctly (101: Inventory used only get_ticket_context; 103: Accounting not in plan)", "Inventory used only <code>get_ticket_context</code>" in refl_html and "Accounting was not in my plan at all" in refl)
ck("Usage figures quoted match the run evidence (requests, tool calls, tokens)", all(f"{t['run']['requests']} model requests, {t['run']['tool_calls']} tool calls, {t['run']['input_tokens']+t['run']['output_tokens']:,} tokens" in refl or f"{t['run']['requests']} requests, {t['run']['tool_calls']} tool calls, {t['run']['input_tokens']+t['run']['output_tokens']:,} tokens" in refl for t in rt["tickets"]))
ck("Zero peer delegations and zero refused delegations claim is true in the audit trail", True if (lambda a: not any(r["event"]=="delegation" and r["agent"]!="boss" for r in a if r["run_id"] in [t["run"]["run_id"] for t in rt["tickets"]]) and not any(r["event"]=="delegation_refused" for r in a if r["run_id"] in [t["run"]["run_id"] for t in rt["tickets"]]))(json.loads((ROOT/"output"/"audit_trail.json").read_text())) else False)

# exactly three solvable and three unsolvable examples
ck("Exactly three new SOLVABLE examples", len(re.findall(r'data-kind="solvable"', refl_html)) == 3)
ck("Exactly three new UNSOLVABLE examples, each with missing information/action, new tools and a new agent", len(re.findall(r'<tr data-kind="unsolvable">', refl_html)) == 3 and all(s in refl_html for s in ["Purchasing agent", "Planning agent", "Compliance agent"]))
ck("The Crest Mug example is gone", "Crest Mug" not in refl and "CC-MUG" not in refl)

# solvable examples verified against the live read-only tools / data
import sys as _s; _s.path.insert(0, str(ROOT/"mcp_server")); import queries as q
st = q.check_product_stock("Basic Hoodie", "L", 12)["products"][0]; bo = q.evaluate_bulk_order("CC-HOOD-NAVY", "L", 12)
ck("Solvable 1 data: hoodie size L has 14 on hand, 12 is feasible, $58 list, order $696, cost $264", st["sku"]=="CC-HOOD-NAVY" and st["requested_size"]["qty_on_hand"]==14 and st["requested_size"]["available"] and bo["list_price"]==58.0 and bo["order_value_at_list"]==696.0 and bo["order_cost"]==264.0 and all(s in refl for s in ["14 on hand","$696.00","$264.00"]))
cp = q.get_cash_position()
ck("Solvable 2 data: $1,000 checking, rent counted paid, $840 unpaid, $160 left", cp["total_cash"]==1000.0 and cp["next_rent_due"][0]["period_paid"] and cp["unpaid_invoices_total"]==840.0 and cp["cash_after_all_listed_obligations"]==160.0)
rc = q.get_rent_obligation(lease_id=1, claimed_amount=2600, claimed_due_date="2026-09-01")["leases"][0]
ck("Solvable 3 data: claim check returns amount and date mismatch; September already paid (payment 1)", rc["claim_check"]["amount_matches"] is False and rc["claim_check"]["due_date_matches"] is False and rc["period_payment"]["payment_id"]==1)
sys.path.insert(0, str(ROOT/"backend")); import agents
ck("Solvable examples use tools their roles actually have (Inventory/Customer Service check_product_stock, Inventory evaluate_bulk_order, Facilities get_rent_obligation, Accounting cash/invoice tools)", {"inventory","customer_service"} <= {k for k,v in agents.ROLE_TOOLS.items() if "check_product_stock" in v} and "inventory" in [k for k,v in agents.ROLE_TOOLS.items() if "evaluate_bulk_order" in v] and "facilities" in [k for k,v in agents.ROLE_TOOLS.items() if "get_rent_obligation" in v] and {"get_cash_position","get_invoice_status"} <= set(agents.ROLE_TOOLS["accounting"]))
db = sqlite3.connect(f"file:{ROOT/'data'/'campus_customs_new.db'}?mode=ro&immutable=1", uri=True)
vcols = [r[1] for r in db.execute("pragma table_info(vendors)")]
ck("Unsolvable 1 claim holds: vendors has no SKU link or price columns; no purchase-order table", vcols == ["id","name","specialty","lead_days"] and not any("purchase" in r[0] for r in db.execute("select name from sqlite_master where type='table'")))
ck("Unsolvable 2 claim holds: no sales/receipts table; lease next_due not advanced", not any(w in r[0] for r in db.execute("select name from sqlite_master where type='table'") for w in ("sale","receipt","revenue")) and db.execute("select next_due from leases").fetchone()[0]=="2026-09-02")
ck("Unsolvable 3 claim holds: the Ticket 102 'email' is only the one-line note", db.execute("select notes from tickets where id=102").fetchone()[0]=="Email: shop rent due in 2 days.")
ck("No write tool is available to any agent role", all(not (v & {"record_approved_payment","reset_working_database","mark_ticket_resolved"}) for v in agents.ROLE_TOOLS.values()))

# earlier tabs preserved, cash reconciles
ck("Ticket 101, 102, 103 and Cash panels are byte-identical to the pre-Problem-10 page", all(panel(desk, p) == panel(old, p) for p in ("t101","t102","t103","cash")))
ck("Everything outside the Reflection panel and the added CSS is unchanged (Expected and Actual sections included)", desk.replace(refl_html, "").replace(re.search(r"\n  \.reflection \{.*?\.reflection table\.deleg td, \.reflection table\.deleg th \{[^}]*\}\n", desk, re.S).group(0), "") == old.replace(panel(old, "reflection"), ""))
cashtxt = re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",panel(desk,"cash")))
ck("Cash tab still reconciles: $3,400.00 − $2,400.00 = $1,000.00 = cash_accounts", all(s in cashtxt for s in ["$3,400.00","−$2,400.00","$1,000.00"]) and round(3400-2400,2) == db.execute("select balance from cash_accounts").fetchone()[0] == 1000.0)
ck("Reflection quotes the same cash figures", all(s in refl for s in ["$3,400.00","$2,400.00","$1,000.00","invoice 501","still unpaid"]))
ck("No inaccurate-claim phrases (invoice paid / shortage filled / discount approved) in the reflection", not re.search(r"(?i)invoice 501[^.]{0,40}\b(was|has been) paid|(?<!no )(?<!not )discount (was|has been) (approved|authorized)|shortage (was|has been) filled", refl))
ck("No placeholder or secret text in the reflection", not re.search(r"(?i)coming later|TODO|\bTBD\b|lorem|Bearer\s+\S{12,}|sk-[A-Za-z0-9]{12,}", refl))

# no financial or agent activity added
audit = json.loads((ROOT/"output"/"audit_trail.json").read_text())
ck("Audit trail still 241 records with the same three live runs; no new events", len(audit)==241 and sorted({r["run_id"] for r in audit[109:239] if r["event"]=="run_started" and r["agent"]=="boss"})==sorted(t["run"]["run_id"] for t in rt["tickets"]))
ck("Exactly one payment row ($2,400 rent) and $1,000 checking; all tickets resolved", db.execute("select count(*) from payments").fetchone()[0]==1 and db.execute("select amount from payments").fetchone()[0]==2400.0 and db.execute("select balance from cash_accounts").fetchone()[0]==1000.0 and [r[0] for r in db.execute("select status from tickets order by id")]==["resolved"]*3)
base = dict(l.split("  ",1)[::-1] for l in (ROOT/"evidence"/"pre-problem10.sha256").read_text().strip().splitlines())
changed = [n for n, h in base.items() if sha(ROOT/n) != h]
ck("Databases, audit trail, resolved_tickets.json, resolved_board.html, harness.md, smoke evidence, screenshots and run evidence unchanged since before Problem 10", changed == [], str(changed))

# browser: all five tabs
js = r"""
import { chromium } from '%s/frontend/node_modules/playwright-core/index.mjs'
const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const p = await b.newPage({ viewport: { width: 1280, height: 900 } })
const errs = []; p.on('pageerror', e => errs.push(String(e)))
await p.goto('file://%s/output/desk_tickets.html')
const out = { tabs: [] }
for (const id of ['t101','t102','t103','cash','reflection']) {
  await p.click(`#tab-${id}`)
  out.tabs.push([id, await p.evaluate(() => [...document.querySelectorAll('[role=tabpanel]')].filter(x => !x.hidden).map(x => x.id).join(','))])
}
out.reflectionChars = (await p.innerText('#panel-reflection')).length
out.h3 = await p.evaluate(() => [...document.querySelectorAll('#panel-reflection h3')].map(h => h.textContent.split(':')[0]))
out.overflowX = await p.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
await p.click('#tab-t101'); out.actual101 = (await p.innerText('[data-actual="101"]')).includes('ca7516af05d8')
await p.click('#tab-cash'); out.cash = (await p.innerText('#panel-cash')).includes('$1,000.00')
out.errors = errs
console.log(JSON.stringify(out)); await b.close()
""" % (ROOT, ROOT)
f = ROOT/".validate_browser10.mjs"; f.write_text(js)
try:
    br = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=120).stdout.strip().splitlines()[-1])
finally:
    f.unlink(missing_ok=True)
ck("Browser (file://): each of the five tabs shows exactly its own panel", [t[1] for t in br["tabs"]] == [f"panel-{i}" for i in ["t101","t102","t103","cash","reflection"]], str(br["tabs"]))
ck("Browser: Reflection renders (>8000 characters, five Question headings), no page errors, no horizontal overflow", br["reflectionChars"] > 8000 and br["h3"][:5] == [f"Question {i}" for i in range(1,6)] and br["errors"] == [] and not br["overflowX"], f"{br['reflectionChars']} chars")
ck("Browser: Ticket 101 Actual and the Cash tab still render their content", br["actual101"] and br["cash"])

# Problem 9 validation still passes
r9 = subprocess.run([sys.executable, "-B", str(ROOT/"tests"/"validate_problem9.py")], capture_output=True, text=True)
last = r9.stdout.strip().splitlines()[-1]
ck("Existing Problem 9 validation still passes", r9.returncode == 0, last)
print(f"\n{sum(R)}/{len(R)} checks passed"); sys.exit(0 if all(R) else 1)
