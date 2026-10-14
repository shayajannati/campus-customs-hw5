"""Read-only validation of the Problem 9 deliverables. Makes no model calls, payments, resets or database writes.
Run:  .venv/bin/python -B tests/validate_problem9.py
"""
import base64, hashlib, json, re, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = []
def ck(name, cond, detail=""):
    R.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
IDS = {101: "ca7516af05d8", 102: "832a7ef51ea1", 103: "88cabc37a99b"}

db = sqlite3.connect(f"file:{ROOT/'data'/'campus_customs_new.db'}?mode=ro&immutable=1", uri=True)
q = lambda s: db.execute(s).fetchall()
audit = json.loads((ROOT/"output"/"audit_trail.json").read_text())
desk = (ROOT/"output"/"desk_tickets.html").read_text()
desk0 = (ROOT/"evidence"/"desk_tickets.pre-problem9.html").read_text()
board = (ROOT/"output"/"resolved_board.html").read_text()
rt = json.loads((ROOT/"output"/"resolved_tickets.json").read_text())
harness = (ROOT/"output"/"harness.md").read_text()

# 1-3 databases and audit trail
ck("Original database unchanged (SHA-256)", sha(ROOT/"data"/"campus_customs.db").startswith("23686a90d698f7fa"))
ck("Working DB: all three tickets resolved", [r[1] for r in q("select id,status from tickets order by id")] == ["resolved"]*3)
ck("Working DB: exactly one payment, the $2,400 September rent", q("select * from payments") == [(1,"rent",1202609,2400.0,"checking","2026-08-31","Authorized course operator")])
ck("Working DB: checking exactly $1,000.00", q("select balance from cash_accounts") == [(1000.0,)])
ck("Working DB: invoice 501 open and unpaid", q("select status from invoices where id=501") == [("open",)])
ck("Working DB: hoodie M stock 8, pricing $58 / $22 unchanged", q("select qty from inventory where sku='CC-HOOD-NAVY' and size='M'") == [(8,)] and q("select list_price,unit_cost from pricing where sku='CC-HOOD-NAVY'") == [(58.0,22.0)])
ck("Working DB: lease next_due not advanced", q("select next_due from leases") == [("2026-09-02",)])
p107 = json.loads((ROOT/"evidence"/"audit_trail.pre-problem9.json").read_text()); p239 = json.loads((ROOT/"evidence"/"audit_trail.pre-rent-payment.json").read_text())
ck("Audit trail has 241 records; the original 107 and the 239 before the payment are intact", len(audit) == 241 and audit[:107] == p107[:107] and audit[:239] == p239)
ev = collections = None
from collections import Counter
cnt = Counter(r["event"] for r in audit[107:])
ck("No extra resets, payments or runs (2 reset, 2 payment records, exactly 3 live runs)", cnt["reset_attempt"]==cnt["reset_committed"]==1 and cnt["payment_attempt"]==cnt["payment_committed"]==1 and sorted({r["run_id"] for r in audit[109:239] if r["event"]=="run_started" and r["agent"]=="boss"}) == sorted(IDS.values()))
ck("All real run records are live gpt-6-luna (no stubs)", all(r["run_mode"]=="live" and r["model"] in (None,"gpt-6-luna") for r in audit[109:239]))
ck("Run record counts 47 / 36 / 47", [len([r for r in audit if r["run_id"]==IDS[t]]) for t in IDS] == [47,36,47])

# 4 Expected sections unchanged
def expected(h, t):
    panel = re.search(rf'<section id="panel-t{t}".*?(?=<section id="panel-t|<section id="panel-cash")', h, re.S).group(0)
    return re.search(r'<section class="card expected".*?</section>', panel, re.S).group(0)
ck("Expected sections byte-identical to the pre-Problem-9 page", all(expected(desk, t) == expected(desk0, t) for t in IDS))
ck("Pre-Problem-9 page copy is the original (hash recorded earlier)", sha(ROOT/"evidence"/"desk_tickets.pre-problem9.html").startswith("137238b4d683af51"))

# 5 Actual sections match the evidence
for t, rid in IDS.items():
    evid = json.loads((ROOT/"evidence"/f"ticket{t}_run.json").read_text())
    act = re.search(rf'<div class="actual-body filled" data-actual="{t}".*?</div>\s*</section>', desk, re.S).group(0)
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", act))
    recs = evid["audit_records"]
    dels = [(r["agent"], r["details"]["to"]) for r in recs if r["event"] == "delegation"]
    tools = {x for r in recs if r["event"]=="tool_call" and r["details"]["tool"] not in ("delegate","final_result") for x in [r["details"]["tool"]]}
    u = evid["run_status"]["usage"]
    ok = rid in txt and all(f"{a.replace('_',' ').title()} → {b.replace('_',' ').title()}" in txt for a, b in dels) and all(tool in txt for tool in tools) \
         and f"{u['requests']} model requests" in txt and f"{u['tool_calls']} tool calls" in txt and f"{len(recs)} audit records" in txt \
         and evid["run_status"]["decision"]["summary"][:80] in txt.replace("&#x27;", "'") or evid["run_status"]["decision"]["summary"][:60] in txt
    ck(f"Ticket {t} Actual: run id, delegations, tools, usage and recommendation match evidence", ok)
    ck(f"Ticket {t} Actual: separates workflow resolved from business status and has an Expected-vs-actual list", "Workflow:" in txt and "Business:" in txt and "Expected versus actual" in txt)
ck("Ticket 102 Actual identifies payment #1, $2,400.00, period 2026-09, approver", all(s in re.sub(r"<[^>]+>"," ",desk) for s in ["payment #1","$2,400.00","2026-09","Authorized course operator","7b57f208fd1c","0c75ad7679ee"]))
ck("Actual sections do not claim invoice 501 paid, shortage filled or a discount authorized", not re.search(r"(?i)invoice 501[^.<]{0,40}\b(was|has been) paid|(?<!no hoodie )(?<!not )shortage (was|has been) filled|(?<!no )discount (was|has been) (approved|authorized)", desk))

# 6 Cash tab
cash = re.search(r'<section id="panel-cash".*?</section>', desk, re.S).group(0)
ct = re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",cash))
ck("Cash tab: starting $3,400.00, ticket changes $0 / −$2,400 / $0, ending $1,000.00", all(s in ct for s in ["$3,400.00","−$2,400.00","$1,000.00","Ticket 101","Ticket 102","Ticket 103"]) and "Coming later" not in ct)
ck("Cash tab arithmetic: 3400 + 0 − 2400 + 0 = 1000 = cash_accounts", round(3400.0+0.0-2400.0+0.0,2) == q("select balance from cash_accounts")[0][0] == 1000.0 and "= $1,000.00" in ct.replace("= <strong>","= ").replace("</strong>",""))
ck("Cash tab states invoice 501 is still open and no hoodie/discount change", "still open and unpaid" in ct and "not an approved discount floor" in ct)
_refl = re.search(r'<section id="panel-reflection".*?</section>', desk, re.S).group(0)
ck("Reflection tab is not a Problem 9 item: still the placeholder, or (after Problem 10) the finished reflection", "Coming later" in _refl or (_refl.count("<h3>Question ") == 5 and len(_refl) > 8000))
ck("Exactly five tabs remain", re.findall(r'role="tab"[^>]*>([^<]+)</button>', desk) == ["Ticket 101","Ticket 102","Ticket 103","Cash","Reflection"])

# 7 resolved_tickets.json
ck("resolved_tickets.json: exactly tickets 101, 102, 103, all resolved with outcomes", [t["ticket_id"] for t in rt["tickets"]] == [101,102,103] and all(t["final_status"]=="resolved" and t["workflow_resolved"] and len(t["outcome"])>40 for t in rt["tickets"]))
need = {"ticket_id","subject","final_status","outcome","business_state","outstanding_business_decisions","run","agents","delegations","expected_vs_actual","human_approvals","human_approval_statement","cash_change","boss_recommendation"}
ck("resolved_tickets.json: consistent schema (every ticket has the same keys)", all(need <= set(t) for t in rt["tickets"]))
ck("resolved_tickets.json: run ids and agent contributions match evidence", all(t["run"]["run_id"]==IDS[t["ticket_id"]] and all(a["contribution"] for a in t["agents"]) and {a["agent"] for a in t["agents"]} == ({a for a in json.loads((ROOT/'evidence'/f"ticket{t['ticket_id']}_run.json").read_text())["audit_records"] and {r["agent"] for r in json.loads((ROOT/'evidence'/f"ticket{t['ticket_id']}_run.json").read_text())["audit_records"]}} - {"system"}) for t in rt["tickets"]))
t102 = rt["tickets"][1]
ck("resolved_tickets.json: only Ticket 102 has a human approval (payment 1, $2,400.00, 2026-09)", [len(t["human_approvals"]) for t in rt["tickets"]] == [0,1,0] and t102["human_approvals"][0]["payment_id"]==1 and t102["human_approvals"][0]["amount"]==2400.0 and t102["human_approvals"][0]["billing_period"]=="2026-09")
ck("resolved_tickets.json: tickets 101 and 103 state no approval was granted", all("No human" in rt["tickets"][i]["human_approval_statement"] for i in (0,2)))
ck("resolved_tickets.json: cash reconciliation 3400 → 1000", rt["cash_reconciliation"]["starting_balance"]==3400.0 and rt["cash_reconciliation"]["ending_balance"]==1000.0 and rt["cash_reconciliation"]["payments_rows"]==1 and sum(rt["cash_reconciliation"]["ticket_changes"].values())==-2400.0)

# 8 board images byte-for-byte
imgs = re.findall(r'src="data:image/png;base64,([A-Za-z0-9+/=]+)"', board)
ck("resolved_board.html has exactly three embedded images", len(imgs) == 3)
ck("Embedded images are byte-identical to the evidence screenshots", [hashlib.sha256(base64.b64decode(i)).hexdigest() for i in imgs] == [sha(ROOT/"evidence"/f"screenshot_ticket{t}.png") for t in IDS])
ck("Board has no external or local-file references (works offline)", not re.search(r'(?:src|href)="(?!#|data:)', board) and "http://" not in re.sub(r'data:image/png;base64,[A-Za-z0-9+/=]+',"",board) and "https://" not in board)
ck("Board labels each ticket and explains workflow vs business", all(f"Ticket {t}:" in board for t in IDS) and "Resolved” means the agent workflow finished" in board)

# 9 browser: tabs and offline images
js = r"""
import { chromium } from '%s/frontend/node_modules/playwright-core/index.mjs'
const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const out = {}
const p = await b.newPage({ viewport: { width: 1280, height: 900 } })
const errs = []; p.on('pageerror', e => errs.push(String(e)))
await p.goto('file://%s/output/desk_tickets.html')
out.tabs = []
for (const id of ['t101','t102','t103','cash','reflection']) {
  await p.click(`#tab-${id}`)
  out.tabs.push([id, await p.evaluate(i => [...document.querySelectorAll('[role=tabpanel]')].filter(x => !x.hidden).map(x => x.id).join(','), id)])
}
await p.click('#tab-cash'); out.cashText = (await p.innerText('#panel-cash')).slice(0, 200)
await p.click('#tab-t102'); out.actual102 = (await p.innerText('[data-actual="102"]')).includes('832a7ef51ea1')
out.pageErrors = errs
const q = await b.newPage({ viewport: { width: 1280, height: 900 } })
const blocked = []
await q.route('**/*', r => { const u = r.request().url(); if (u.startsWith('file:') || u.startsWith('data:')) r.continue(); else { blocked.push(u); r.abort() } })
await q.goto('file://%s/output/resolved_board.html')
await q.waitForLoadState('load')
out.images = await q.evaluate(() => [...document.images].map(i => [i.complete, i.naturalWidth, i.naturalHeight]))
out.blockedRequests = blocked
console.log(JSON.stringify(out)); await b.close()
""" % (ROOT, ROOT, ROOT)
f = ROOT/".validate_browser.mjs"; f.write_text(js)
try:
    res = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=120)
    br = json.loads(res.stdout.strip().splitlines()[-1])
finally:
    f.unlink(missing_ok=True)
ck("desk_tickets.html: each of the five tabs shows exactly its own panel (opened from file://)", [t[1] for t in br["tabs"]] == [f"panel-{i}" for i in ["t101","t102","t103","cash","reflection"]], str(br["tabs"]))
ck("desk_tickets.html: Actual section visible in the page and the page has no script errors", br["actual102"] and br["pageErrors"] == [])
ck("resolved_board.html offline: all three images decode (width/height > 0) with every network request blocked", len(br["images"])==3 and all(i[0] and i[1]>1000 and i[2]>1000 for i in br["images"]) and br["blockedRequests"]==[], str(br["images"]))

# 10-12 hygiene and documentation
allout = "\n".join(p.read_text(errors="ignore") for p in [ROOT/"output"/"desk_tickets.html", ROOT/"output"/"resolved_tickets.json", ROOT/"output"/"harness.md", ROOT/"output"/"design.md", ROOT/"AI_prompts.md"] + list((ROOT/"evidence").glob("*.json")))
ck("No secrets: no bearer tokens, API keys or admin-token assignments in deliverables or evidence", not re.search(r"Bearer\s+[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9]{16,}|CAMPUS_ADMIN_TOKEN=['\"]?[A-Za-z0-9_\-]{20,}", allout.replace("a-long-random-secret-of-16+-characters","")))
ck("No placeholder text in the finished deliverables (outside the Reflection tab)", not re.search(r"to be completed in Problem 9|intentionally empty|TODO|\bTBD\b|lorem", re.sub(r"base64,[A-Za-z0-9+/=]+", "", desk + board + json.dumps(rt)), re.I))
need_h = ["nine SQLite tables","12 read-only agent-facing tools","ledger write mechanism","five agents","Delegation","API routes","React dashboard","Audit trail","Cash reconciliation","Security and safety","uvicorn main --reload --port 8000","8766","Workflow resolution is not business authorization","7b57f208fd1c"]
ck("harness.md covers tables, tools, agents, routes, dashboard, audit, cash and safety, the start command and the port note", all(s.lower() in harness.lower() for s in need_h), str([s for s in need_h if s.lower() not in harness.lower()]))
ck("harness.md lists all nine tables", all(f"| `{t}` |" in harness for t in ["desk","inventory","pricing","vendors","invoices","leases","cash_accounts","payments","tickets"]))
ck("Evidence files all present", all((ROOT/"evidence"/n).is_file() for n in ["ticket101_run.json","ticket102_run.json","ticket103_run.json","screenshot_ticket101.png","screenshot_ticket102.png","screenshot_ticket103.png","payment_verification_rent_2026-09.json","SHA256SUMS.txt","campus_customs_new.pre-problem9.db","audit_trail.pre-problem9.json"]))
print(f"\n{sum(R)}/{len(R)} checks passed"); sys.exit(0 if all(R) else 1)
