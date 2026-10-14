# Campus Customs MCP Server

This FastMCP server gives Campus Customs AI agents **read-only** access to the store's database, so they can answer questions from real records instead of guessing.

## Database

- **File:** `HW 5/data/campus_customs_new.db` (the working copy; the original `campus_customs.db` is never touched)
- **How the path is found:** it's built from this folder's own location, so the server finds the database no matter which folder you start it from.
- **Safety:** the database is opened in SQLite read-only mode (`mode=ro` plus `PRAGMA query_only = ON`). All queries use `?` parameters, and every tool is marked `readOnlyHint: true`.

## Tools (12, all read-only)

| Tool | Inputs | Purpose |
|---|---|---|
| `check_product_stock` | `product` (SKU or part of the name), `size` (optional), `quantity` (default 1) | Stock by size, shelf location, and list price. Answers "can we fill this order?" (Ticket 101) |
| `get_rent_obligation` | `lease_id`, `landlord`, `claimed_amount`, `claimed_due_date` (all optional) | The store's recorded rent, landlord, and due date, with a check against what an email claims (Ticket 102). Never pays anything. |
| `evaluate_bulk_order` | `sku`, `size`, `quantity` | On-hand quantity and shortfall, list price, unit cost, and the largest discount before a loss (Ticket 103). Never approves a discount or reserves stock. |
| `get_business_date` | none | The store's own "today" from the `desk` table (2026-08-31), used to judge what is overdue. |
| `list_open_tickets` | none | All open tickets (type, subject, SKU, size, quantity, linked lease/invoice, notes). Requester names are withheld. |
| `get_ticket_context` | `ticket_id` | One ticket plus its linked stock and price, invoice (with days past due), and lease. Requester name withheld. |
| `get_vendor_info` | `vendor_id`, `name` (both optional) | Vendor name, specialty, and lead time in days. |
| `get_invoice_status` | `invoice_id`, `vendor_id`, `status` (all optional) | Invoice amount, due date, status, vendor, days past due at the business date, and payments that appear to match it. The matching rule is an unverified assumption (the schema doesn't define `payments.kind` / `ref_id`); the result says so. Never pays anything. |
| `get_cash_position` | none | Cash balances next to unpaid invoices and next rent due. Information only. |
| `get_payment_history` | none | Recorded payments (currently none). |
| `list_all_tickets` | none | Every ticket, any status, with `state` (`open` or `resolved`) and, since Problem 8, a read-only derived `business` block (payment state, pending items). Requester names withheld. Used by the API's `GET /tickets`. |
| `get_customer_safe_ticket_context` | `ticket_id` | A ticket plus customer-safe product facts only: list price, stock by size, shortfall. Never returns unit cost, margins, invoices, vendors, leases, cash or the requester name. Used by the Customer Service agent. |

All twelve tools are read-only. (Problem 7 hardening added fields to three of them, without removing or changing any earlier field: `get_payment_history` rows gain `meaning`, `get_rent_obligation` leases gain `period_payment`, and `get_cash_position` no longer counts rent that is already paid for its billing period.) The first three (`check_product_stock`, `get_rent_obligation` and `evaluate_bulk_order`, for Tickets 101, 102 and 103) are the original Problem 3 tools. Their earlier behavior is unchanged; the only change is the additive `period_payment` field on `get_rent_obligation` noted above. Eight more were added in Problem 5 for the agent team, and `list_all_tickets` in Problem 7.

Tables read: `inventory`, `pricing`, `leases`, `desk`, `tickets`, `invoices`, `vendors`, `cash_accounts`, `payments`. See `output/harness.md` (section 5.4) for the table list per tool.

Invalid inputs (empty text, a quantity below 1, a badly formatted date) return a clear tool error. When no record matches, the result has `"found": false` and a message.

## Files

- `server.py` defines the FastMCP server and registers the twelve read-only tools.
- `queries.py` holds the read-only SQL logic, which can be tested without starting the server.
- `requirements.txt` lists the dependencies.
- `ledger.py` and `ledger_server.py` are the **separate, write-capable ledger service** (see below). `server.py` and `queries.py` never import them.

## Dependencies

- Python 3.10+
- `fastmcp==4.0.10`, pinned to the version whose API the code was written against (the same version as the course's `lecture 9` environments)
- `sqlite3` comes with Python, so it doesn't need to be installed.

```bash
pip install -r mcp_server/requirements.txt
python mcp_server/server.py      # starts the server over stdio
```

## Ledger service (Problem 7): NOT for agents

`ledger_server.py` is a second MCP server, `campus-customs-ledger`, with three tools: `record_approved_payment`, `reset_working_database` and `mark_ticket_resolved` (open to resolved only, idempotent; it records that a ticket's agent workflow finished and does not pay anything). It writes to `data/campus_customs_new.db` (and only that file). It exists only for the FastAPI routes `POST /payments/approve`, `POST /database/reset` and the asynchronous ticket run, which require an admin token. **Never register it with a PydanticAI agent or add it to `.mcp.json`.** The agent-facing server above stays read-only. See `output/harness.md`, section "Problem 7", for the rules.
