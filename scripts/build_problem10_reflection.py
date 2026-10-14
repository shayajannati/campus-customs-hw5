"""Inserts the approved Problem 10 reflection into the Reflection tab of output/desk_tickets.html.
Only the Reflection panel (and a small additive CSS block) change. Read-only on everything else:
no database access, no agent runs, no payments."""
import hashlib, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
page = (ROOT / "output" / "desk_tickets.html").read_text()

REFLECTION = r'''<h2>Reflection</h2>
    <div class="reflection">
    <p class="lead">I started this project wanting to know whether I could trust a small team of AI agents with a shop's real decisions. All three tickets ended with the status &ldquo;resolved&rdquo;, but in this reflection &ldquo;resolved&rdquo; means only that <strong>the agent workflow completed</strong>. It does not mean the underlying business issue was solved: invoice 501 ($840) is still unpaid, two stock shortages are unfilled, no discount was approved, and the only cash that moved was one rent payment that I approved myself.</p>

    <h3>Question 1: How I evaluate the agents on each ticket</h3>
    <p>I did not just ask whether the agents were right. I asked whether they stayed in their lane, whether they sent the right decisions to a person, and what it cost to get there. On that standard the agents were careful but not efficient.</p>

    <h4>Ticket 101 (Bulldog tee, size S)</h4>
    <ul>
      <li><strong>What they got right.</strong> They found size S at 0 on hand and invoice 501 at $840, 3 days past due. Accounting reminded me that an empty payments table does not prove nothing was paid outside the system. Customer Service wrote a reply with the $28.00 list price that promised nothing about a restock. Boss escalated three separate decisions: pay the invoice, authorize a reprint, and make any promise to the customer.</li>
      <li><strong>A judgment call that impressed me.</strong> The team noticed an $840 reprint bill against a $28 sale and said that needs a person's judgment.</li>
      <li><strong>What I would criticize.</strong> Inventory made one tool call, <code>get_ticket_context</code>, which Boss had already made, and that call already contains the stock and the shortfall. So that delegation mostly repeated what Boss knew.</li>
      <li><strong>Cost.</strong> 11 model requests, 13 tool calls, 31,244 tokens, about 41 seconds.</li>
    </ul>

    <h4>Ticket 102 (rent)</h4>
    <ul>
      <li><strong>What they got right.</strong> Facilities confirmed the lease at $2,400 due 2026-09-02, two days out and not overdue. It also saw that the email note names no landlord and no amount, so those claims &ldquo;cannot be compared&rdquo;. That was the instinct I wanted: the lease is the authority, not an email. Accounting added that paying rent alone would leave $1,000, and rent plus invoice 501 would leave $160. Only one decision came to me: whether to pay.</li>
      <li><strong>What I would criticize.</strong> <code>get_ticket_context</code> ran three times (Boss, Facilities, Accounting) and <code>get_business_date</code> twice.</li>
      <li><strong>Cost.</strong> 9 requests, 10 tool calls, 25,037 tokens, about 50 seconds.</li>
      <li><strong>The human step.</strong> After the run I approved one simulated payment (payment 1). It is the only cash movement in the whole exercise, and the Cash tab shows it: $3,400.00 to $1,000.00.</li>
    </ul>

    <h4>Ticket 103 (Yale AI Club hoodies)</h4>
    <ul>
      <li><strong>What they got right.</strong> 20 requested, 8 on hand, shortfall 12, $58 list, $22 cost. Accounting said to keep the $36 margin &ldquo;as a verified fact only&rdquo; and not to treat the cost as an approved discount floor. The customer draft has 8 and $58 and no cost, margin or cash figure.</li>
      <li><strong>What I would criticize.</strong> <code>evaluate_bulk_order</code> ran twice (Inventory and Accounting). Accounting also pulled the overdue invoice and rent into a discount ticket. That was useful context but a little off-topic, and it came from the Boss prompt's instruction to check cash, which I wrote.</li>
      <li><strong>The customer draft.</strong> It says &ldquo;We're reviewing your bulk discount request and checking whether the remaining 12 can be fulfilled&rdquo; and then, correctly, &ldquo;we can't confirm the additional quantity or a delivery date yet.&rdquo; I do not think this is a failure, because it states its own limits. But a customer could read &ldquo;reviewing your discount&rdquo; as a discount being considered, and &ldquo;checking whether the remaining 12 can be fulfilled&rdquo; as someone sourcing hoodies. Nobody has decided to do either. Every draft goes to a human before it is sent, so I call this a tone risk to edit, not a confirmed error.</li>
      <li><strong>Cost.</strong> 11 requests, 13 tool calls, 32,627 tokens, about 44 seconds.</li>
    </ul>

    <div class="callout"><strong>The bigger point.</strong> Three workflows completed, but the business is mostly unresolved. Invoice 501 is still unpaid, the tee shortage and the 12-hoodie shortage are unfilled, and no discount was approved. In all three runs Boss's status was &ldquo;needs human approval&rdquo;. Only rent moved money. I rate the agents high on accuracy and restraint, and middling on efficiency.</div>

    <h3>Question 2: Actual versus Expected</h3>
    <p>What I expected was chains: a specialist finishing and handing work to the next one. What happened was a <strong>hub</strong>. Boss delegated to every specialist itself in all three runs, and no specialist ever delegated to another (zero peer delegations, zero refused delegations).</p>
    <table class="deleg" data-testid="comparison">
      <thead><tr><th>Ticket</th><th>I expected</th><th>What happened</th><th>Tool differences</th></tr></thead>
      <tbody>
        <tr data-ticket="101"><td>101</td><td>Boss &rarr; Inventory &rarr; Accounting; Customer Service not called</td><td>Boss &rarr; Inventory, Boss &rarr; Accounting, Boss &rarr; Customer Service</td><td>All five expected tools were used, but <code>check_product_stock</code> was used by Customer Service while Inventory used only <code>get_ticket_context</code>. Extras: <code>get_cash_position</code>, <code>get_payment_history</code> (Accounting) and <code>get_customer_safe_ticket_context</code> (Customer Service).</td></tr>
        <tr data-ticket="102"><td>102</td><td>Boss &rarr; Facilities &rarr; Accounting</td><td>Boss &rarr; Facilities, Boss &rarr; Accounting</td><td>The tools matched exactly: none missing and none extra. Only the hand-off differed (Facilities never delegated to Accounting).</td></tr>
        <tr data-ticket="103"><td>103</td><td>Boss &rarr; Inventory &rarr; Customer Service</td><td>Boss &rarr; Inventory, Boss &rarr; Accounting, Boss &rarr; Customer Service</td><td>All four expected tools were used. Extras: <code>check_product_stock</code> (Customer Service), and <code>get_cash_position</code>, <code>get_payment_history</code>, <code>get_invoice_status</code> (Accounting). Accounting was not in my plan at all.</td></tr>
      </tbody>
    </table>
    <p>The first specialist matched all three times (Inventory, Facilities, Inventory). On 101 my plan was too timid: I held Customer Service back, but its draft promised nothing. I suspect the Boss prompt's rule to consult Accounting before a financial commitment is why Accounting joined 102 and 103, but the audit trail shows what happened, not why.</p>

    <h4>What the hub meant for me as a manager</h4>
    <ul>
      <li><strong>Accountability was clearer.</strong> Every task Boss gave is on the record, each report is attributed to one agent, and Boss owns the final recommendation. The flip side is that Boss's wording of each task shaped the answer. Accounting's task on 102 explicitly asked about &ldquo;competing obligations&rdquo;, and that is exactly what came back.</li>
      <li><strong>Coordination.</strong> Boss had the whole picture, which is how one summary could say rent plus invoice 501 leaves $160. But the specialists never used the ability to raise something with a colleague, so Facilities could not pull Accounting in on its own judgment. The loop and chain-length guards I built were never exercised.</li>
      <li><strong>Efficiency.</strong> Boss made about 4 of the 9 to 11 requests in each run (approximate, derived from cumulative usage), so roughly a third to a half of the model calls were coordination. On 101 and 103 Boss sent the Inventory and Accounting tasks in the same second and Customer Service about 19 and 15 seconds later, after it had their reports. On 102 the two delegations were about 14 seconds apart.</li>
    </ul>

    <h3>Question 3: What would have been simpler as one agent with tools</h3>
    <p>The honest answer is that the <strong>retrieval</strong> was the wasteful part, not the specialists. The same facts were fetched more than once in a run: <code>get_ticket_context</code> twice on 101 and 103 and three times on 102, <code>get_business_date</code> twice on 101 and 102, and <code>evaluate_bulk_order</code> twice on 103. That part would be simpler.</p>
    <p>The roles are a different matter. A single agent holding every tool would also have to be trusted with unit cost while writing customer text. Customer Service shows why I would not merge it: its tools are <code>get_customer_safe_ticket_context</code> and <code>check_product_stock</code>, and the MCP server itself never returns the $22 cost to it. In Ticket 103 its draft had only 8 and $58. That protection is built into the permissions, not just the prompt. By contrast, Inventory on 101 and the split between Facilities and Accounting were more about organization than safety.</p>
    <p>Rather than removing agents, I would try <strong>shared evidence</strong> first. Boss could run <code>get_ticket_context</code> and <code>get_business_date</code> once and pass the results to the specialists in the task, or the app could keep a per-run cache of read-only tool results keyed by tool and arguments. These are reads of the same database within seconds, so a cache is plausible. It would need clearing whenever something is written, such as a payment or a ticket resolving, because the ticket context includes payment state.</p>
    <p>I cannot claim any version would be faster or cheaper. I never ran a single-agent baseline, and I do not know how much of each request's cost is the fixed instructions versus the tool results.</p>

    <h3>Question 4: Three new problems the current team could handle</h3>
    <p>I checked each against the real tables and the roles' tool lists.</p>
    <ol class="examples">
      <li data-kind="solvable"><strong>Is a proposed order feasible? 12 size-L hoodies.</strong> The product is <code>CC-HOOD-NAVY</code> (&ldquo;Basic Hoodie Big Yale&rdquo;), size L, with 14 on hand in Aisle A. <code>check_product_stock</code> reports it available with no shortfall, and <code>evaluate_bulk_order</code> shows $58.00 list, an order value of $696.00 and a cost of $264.00. Inventory (both tools) and Customer Service (<code>check_product_stock</code>) have access. Unlike Ticket 103, the stock covers it. But the team can only say it is feasible: it cannot reserve the hoodies or fulfill the order, and any discount stays a human decision.</li>
      <li data-kind="solvable"><strong>A cash planning question after the rent payment.</strong> A manager asks whether we can pay invoice 501 now. <code>get_cash_position</code> shows $1,000 in checking, the September rent already counted as paid, and $840 unpaid; <code>get_invoice_status</code> shows it 3 days past due. Accounting can answer that paying it would leave $160 and recommend that a human decide, since the team has no way to pay anything. This question is about the shop's cash, not about a customer.</li>
      <li data-kind="solvable"><strong>A conflicting rent demand.</strong> The landlord's follow-up claims $2,600 due 2026-09-01. <code>get_rent_obligation</code> accepts a claimed amount and date. Run against lease 1 it returns <code>amount_matches: false</code> and <code>due_date_matches: false</code> against the recorded $2,400 due 2026-09-02, and it shows September already paid (payment 1). Facilities has that tool. In the real Ticket 102 run it had nothing to compare, because the note stated no amount. The team can flag the mismatch and escalate, but it cannot contact the landlord.</li>
    </ol>

    <h3>Question 5: Three new problems the current team could not handle</h3>
    <table class="deleg" data-testid="unsolvable">
      <thead><tr><th>New problem</th><th>What is missing</th><th>New tools</th><th>New agent</th></tr></thead>
      <tbody>
        <tr data-kind="unsolvable"><td><strong>1. Actually restocking</strong> the 12 missing size-M hoodies (or the size-S tee)</td><td><em>Information:</em> which vendor supplies which SKU (the <code>vendors</code> table has only a name, specialty and lead days), vendor prices and minimum quantities (invoices carry only a description). <em>Action:</em> any way to place an order; the ledger handles only invoice and rent payments and ticket status.</td><td><code>get_supplier_options(sku)</code> (read). Behind the protected, human-approved backend: <code>draft_purchase_order</code>, <code>approve_purchase_order</code> and <code>receive_stock</code>, with a new <code>purchase_orders</code> table.</td><td>A <strong>Purchasing agent</strong> with read-only access that can prepare a <em>draft</em> purchase order. Authorizing a purchase and any inventory-changing action would happen only through separate protected backend operations that a human approves, never through the agent's own tools.</td></tr>
        <tr data-kind="unsolvable"><td><strong>2. Forecasting cash:</strong> &ldquo;Can we cover October's rent and invoice 501 if sales stay normal?&rdquo;</td><td><em>Information:</em> I have one balance ($1,000 after the rent payment), one lease and one invoice. There is no sales or receipts data and no schedule of future obligations, and I deliberately did not advance <code>next_due</code> because no business rule defines it.</td><td>A <code>sales</code> table with <code>get_sales_summary</code>, <code>forecast_cash(weeks)</code>, and a human-approved <code>advance_lease_period</code>.</td><td>A <strong>Planning agent</strong> (read-only) that proposes a forecast; the lease change stays a protected human-approved operation.</td></tr>
        <tr data-kind="unsolvable"><td><strong>3. Deciding whether a payment request is genuine.</strong></td><td><em>Information:</em> the team can check an amount against the lease (example 3 above) but cannot check <em>who sent it</em>. Ticket 102's &ldquo;email&rdquo; was only the database note &ldquo;Email: shop rent due in 2 days&rdquo;, with no sender, header or bank detail, so a message saying &ldquo;we changed our bank account&rdquo; would defeat the team. <em>Action:</em> Customer Service drafts replies, but nothing can send one.</td><td><code>read_inbound_message</code>, a verified payee directory, and <code>flag_suspicious_request</code> (write to a review queue).</td><td>A <strong>Compliance agent</strong> that reads messages and flags them for a person.</td></tr>
      </tbody>
    </table>

    <h3>Why read-only tools and protected human approval mattered</h3>
    <p>No agent role has a write tool. The agents could recommend things, including a $2,400 payment, but the only thing that moved cash was payment 1, made through a protected route that needs an admin token and an approver name. It revalidates the amount against the database, blocks duplicates, and leaves an audit trail (<code>payment_attempt</code> 7b57f208fd1c and <code>payment_committed</code> 0c75ad7679ee). The agents' own warning that paying rent and invoice 501 together leaves $160 is exactly the tradeoff I want a person to decide. That separation is also why &ldquo;resolved&rdquo; on the ticket board is safe to show: it cannot be mistaken for permission to spend.</p>

    <table class="deleg cashtab" data-testid="cash-evidence">
      <thead><tr><th>Cash evidence (from the Cash tab)</th><th class="num">Amount</th></tr></thead>
      <tbody>
        <tr><td>Starting checking balance</td><td class="num">$3,400.00</td></tr>
        <tr><td>Ticket 101: no payment approved</td><td class="num">$0.00</td></tr>
        <tr><td>Ticket 102: one human-approved rent payment (payment 1)</td><td class="num">&minus;$2,400.00</td></tr>
        <tr><td>Ticket 103: no discount, purchase or commitment approved</td><td class="num">$0.00</td></tr>
        <tr class="total"><td>Ending checking balance ($3,400.00 &minus; $2,400.00)</td><td class="num">$1,000.00</td></tr>
      </tbody>
    </table>
    <p class="muted">Invoice 501 ($840.00) remains open and unpaid. The audit trail has 241 records, including 47, 36 and 47 for the three runs.</p>

    <h3>Limits of this evaluation</h3>
    <p class="muted">Each ticket ran once, so my judgments are not a benchmark. The per-agent request split and the elapsed times come from the audit trail and are approximate. I did not run a single-agent baseline or test the caching idea. The three solvable examples were checked by running the read-only tools on the current database, not by running the agents, so they describe what the team could do, not what it did.</p>
    </div>'''

css = """
  .reflection { max-width: 980px; }
  .reflection h3 { margin: 26px 0 8px; padding-top: 10px; border-top: 2px solid var(--line); color: var(--navy); font-size: 1.2rem; }
  .reflection h4 { margin: 18px 0 6px; }
  .reflection .lead { font-size: 1.05rem; background: var(--pale-blue); border-left: 4px solid var(--blue); padding: 12px 16px; border-radius: 6px; }
  .reflection ul, .reflection ol { padding-left: 1.3rem; } .reflection li { margin-bottom: .45rem; }
  .reflection table.deleg td, .reflection table.deleg th { vertical-align: top; }
"""
pat = re.compile(r'(<section id="panel-reflection"[^>]*>)\s*<h2>Reflection</h2>\s*<p class="coming">Coming later\.</p>\s*(</section>)', re.S)
assert pat.search(page), "Reflection placeholder not found (already inserted?)"
new = pat.sub(lambda m: m.group(1) + "\n    " + REFLECTION + "\n  " + m.group(2), page, count=1)
assert css.strip() not in new
new = new.replace("</style>", css + "</style>", 1)
(ROOT / "output" / "desk_tickets.html").write_text(new)

# the other four panels and every Expected section must be byte-identical
old = page
def panel(h, pid):
    return re.search(rf'<section id="panel-{pid}".*?(?=<section id="panel-|</main>)', h, re.S).group(0)
for pid in ("t101", "t102", "t103", "cash"):
    assert hashlib.sha256(panel(old, pid).encode()).hexdigest() == hashlib.sha256(panel(new, pid).encode()).hexdigest(), pid
print("reflection inserted; ticket 101/102/103 and Cash panels byte-identical")
