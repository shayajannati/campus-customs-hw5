# Campus Customs: five-agent operations desk (MGT 409, Homework 5)

A small AI-assisted back office for a campus shop. Five PydanticAI agents (all using the `gpt-6-luna` model through Portkey) work on three open tickets, using **read-only MCP tools** over a SQLite database. People, not agents, approve any money movement.

| Ticket | Situation |
|---|---|
| 101 | A customer wants a Bulldog tee in size S (0 in stock) and a vendor invoice (501, $840) is overdue. |
| 102 | The landlord's rent notice: $2,400 due 2026-09-02. |
| 103 | The Yale AI Club wants 20 size-M hoodies at a bulk discount (8 in stock). |

## Architecture in one picture

```
React dashboard (frontend/, :5173)  ──HTTP──>  FastAPI backend (backend/main.py, :8000)
                                                   │
                        five agents: Boss, Inventory, Accounting, Facilities, Customer Service
                        (backend/agents.py, prompts in backend/prompts/*.md)
                                                   │ MCP (stdio)
              ┌────────────────────────────────────┴────────────────────────────────┐
   read-only MCP server (mcp_server/server.py)                 ledger MCP server (mcp_server/ledger_server.py)
   12 tools, database opened read-only                         record_approved_payment, reset_working_database,
   (the ONLY tools agents can use)                             mark_ticket_resolved (never given to agents)
              └──────────────── data/campus_customs_new.db (SQLite, 9 tables) ──────────┘
```

* **Boss** reads a ticket and delegates; **Inventory**, **Accounting**, **Facilities** and **Customer Service** answer within their own tools. Customer Service has no tool that can return unit cost or finances.
* Every agent step is appended to `output/audit_trail.json` (one JSON array; never rewritten).
* The ledger server is only called by the backend's protected routes after a human supplies the admin token.
* Full details: `output/harness.md` (tables, tools, agents, routes, safety) and `output/design.md` (dashboard design).

## Important: "resolved" is not "done"

A ticket marked **resolved** means **the agent workflow finished**. It does **not** mean the business problem was solved. In the submitted state all three tickets are resolved, yet:

* invoice 501 ($840.00) is still **open and unpaid**, and the size-S tee is still out of stock;
* the 12-hoodie shortage is unfilled and **no discount** was approved;
* the only money that moved is **one simulated rent payment of $2,400.00** that a human approved (checking: $3,400.00 → $1,000.00). It exists only in the local simulated ledger; no real transfer occurred.

## Prerequisites

* Python 3 and a way to create virtual environments (developed and tested with Python 3.14).
* Node.js and npm (developed and tested with Node 24 and npm 11).
* macOS or Linux shell commands are shown. Chrome is needed only for the optional screenshot script and the validators' browser checks (they look for Google Chrome at its macOS path).
* A Portkey API key is needed **only** to run the agents live. Reading the submitted evidence, starting the servers and running most tests need no key.

## Setup

```bash
# from the repository root
python3 -m venv .venv                 # the MCP config (.mcp.json) expects the environment at ./.venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                  # then edit .env: set PORTKEY_API_KEY and CAMPUS_ADMIN_TOKEN
cd frontend && npm install && cd ..
```

* `.env` lives in the repository root and is **never committed** (it is in `.gitignore`). `.env.example` contains placeholders only. A `.env` one folder above the repository is still read as a fallback; real environment variables always win.
* `CAMPUS_ADMIN_TOKEN` can be any random string of at least 16 characters. The protected routes (run a ticket, approve a payment, reset the database) refuse to work without it.

### `.mcp.json` and the virtual environment

`.mcp.json` registers the read-only server for MCP clients such as Claude Code. It uses portable paths (`${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python` and `${CLAUDE_PROJECT_DIR:-.}/mcp_server/server.py`), so it works **only after you create `.venv` in the repository root as above**. On Windows, edit the command to `.venv\Scripts\python.exe`.

## Start the three pieces

**1. MCP server** (read-only tools over stdio; normally started for you by the backend or an MCP client):

```bash
python mcp_server/server.py
```

**2. FastAPI backend** (run from `backend/`):

```bash
cd backend
uvicorn main:app --reload --port 8000
```

The module is `main` and the FastAPI object is `app`, so the working command is `uvicorn main:app`. The bare form `uvicorn main --reload --port 8000` does **not** start a server with uvicorn 0.54 (it exits with `Import string "main" must be in format "<module>:<attribute>"`). The backend binds to `127.0.0.1`, accepts only localhost Host headers, and allows CORS only from `http://localhost:5173`. If port 8000 is busy, use another port (for example `--port 8766`) and start the frontend with `VITE_API_ORIGIN=http://localhost:8766 npm run dev`.

**3. React frontend:**

```bash
cd frontend
npm run dev                           # http://localhost:5173 (talks to http://localhost:8000)
```

## Run the tickets, review evidence, approve payments

1. Open http://localhost:5173. Reading works without a token.
2. Click **Unlock desk** and paste your `CAMPUS_ADMIN_TOKEN`. It is kept in browser memory only.
3. Select a ticket and click **Run agent team**. This makes live model calls (it needs `PORTKEY_API_KEY` and spends tokens). The timeline shows the real audit records as they are written; when the run finishes normally the ticket becomes *resolved*.
4. If the agents ask for a payment, the **Human approval** panel shows server-verified details (payee, amount, due date, cash before and after). Type a name, tick the box and confirm. Nothing is ever paid automatically.
5. Audit evidence: `output/audit_trail.json` (append-only), and the run reports and screenshots in `evidence/`.

Command-line alternative (live, synchronous, does not mark tickets resolved): `python scripts/run_tickets.py 101 102 103`.

## Start from a clean database

`data/campus_customs.db` is the original, untouched course database. `data/campus_customs_new.db` is the **working copy** that the tools and agents use.

**Option A: copy the file** (stop the backend first):

```bash
cp data/campus_customs.db data/campus_customs_new.db
```

**Option B: the protected reset endpoint** (backend running; run it before a full three-ticket run):

```bash
set -a; source .env; set +a
curl -X POST http://localhost:8000/database/reset \
  -H "Authorization: Bearer $CAMPUS_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"confirm": true}'
```

The endpoint copies the original over the working copy (byte for byte, verifying hashes) and appends reset records to the audit trail. It never changes the original.

> **Warning: resetting overwrites the submitted resolved state.** After a reset, all three tickets are open again, the simulated rent payment is gone and checking is back to $3,400.00. The audit trail is *not* erased (new runs are appended after the 241 submitted records). To restore the submitted state from Git: `git checkout -- data/campus_customs_new.db`.

## What to inspect (deliverables)

| File | What it is |
|---|---|
| `AI_prompts.md` | Every prompt used, by problem, with notes. |
| `output/harness.md` | Tables, MCP tools, agents, API routes, dashboard, audit trail, cash reconciliation, safety. |
| `output/mcp_smoke.json` | Real MCP tool-call evidence for the three original tools. |
| `output/desk_tickets.html` | Double-click to open. Expected plans, Actual results, Cash reconciliation and the Reflection. |
| `output/design.md` | Dashboard design notes. |
| `output/resolved_tickets.json` | Final status, outcome, agent contributions and human approvals per ticket. |
| `output/resolved_board.html` | Double-click to open. Real screenshots of the dashboard for tickets 101, 102 and 103 (works offline). |
| `output/audit_trail.json` | 241 records: earlier test runs, one reset, the three live runs, and the rent payment. |
| `evidence/` | Run reports, screenshots, payment verification and hash files. |
| `mcp_server/README.md` | The MCP server's tools and tables. |

## Tests and validators

* `cd frontend && npm run typecheck && npm run lint && npm test && npm run build`
* `python tests/validate_problem9.py` and `python tests/validate_problem10.py` are read-only and check the submitted deliverables (they also open the HTML in Chrome).
* `python tests/run_local_tests.py` and `python tests/run_api_tests.py` (stub models, no live calls) assume the **original starting data**. Run them in a throwaway copy of the repository where `data/campus_customs_new.db` is a copy of `data/campus_customs.db` and `output/audit_trail.json` is `evidence/audit_trail.pre-problem9.json`. Do not run them against the submitted resolved state.

## Safety summary

Agents only have read-only tools; payments, resets and ticket resolution go through a separate ledger behind an admin token plus explicit human confirmation; amounts come from the database and are revalidated; duplicates and overdrafts are refused; model-request, tool-call and token limits apply per ticket. This is a local development tool, not a production financial system.
