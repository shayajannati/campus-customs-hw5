# Campus Customs Operations Desk: design notes (Problem 8)

The dashboard is a React + Vite + TypeScript app in `frontend/`. It talks to the backend at `http://localhost:8000` (the Vite origin `http://localhost:5173` is the only origin the backend's CORS allows). Start it with `npm run dev`.

The idea: a small **operations desk** where a human sits in charge of five agents. The agents recommend. The human decides. Everything on screen is built from what the backend and the audit trail actually record.

## Layout

A header band, then a board of panels (three columns on desktop, two on tablet, one on a phone).

| Area | What it holds |
|---|---|
| **Header** | The brand, the business date, a large **checking balance** ticker, and the "Unlock desk" control. |
| **Ticket board** (left) | The three tickets (101, 102, 103) as case-file cards. Each card has a "Run agent team" button, a workflow chip, a business chip, and the list of things still outstanding. |
| **Agent floor** (middle) | A delegation map and five agent cards. Each card shows what that agent is doing now and, after a run, a short summary of its work. |
| **Activity timeline** (right) | A live "radio log": one line per audit-trail record, in the order written, updating while a run is in progress. |
| **Run summary** (bottom left) | Boss's recommendation, each specialist's report, a customer-reply draft (labelled as a draft), what is waiting on a human, and what is still outstanding in the business. |
| **Human approval** (bottom right) | The payment review panel (see "Approval" below). |

Selecting a ticket changes the floor, timeline, summary and approval panel to that ticket. After a page reload, the last run of a selected ticket is rebuilt from the audit trail and labelled "recorded history".

## How the five agents are told apart

Colour is never the only cue. Each agent has its **own colour, its own silhouette and its own glyph**, plus a text name and a written state.

| Agent | Silhouette | Glyph | Colour |
|---|---|---|---|
| Boss | hexagon | crown | deep navy |
| Inventory | rounded square | stacked boxes | royal blue |
| Accounting | diamond | dollar sign | indigo blue |
| Facilities | arch | building | teal blue |
| Customer Service | speech bubble | chat dots | light sky blue |

The same badge appears on the agent card, on the delegation map and beside every timeline line. The state is spelled out ("Working", "Waiting", "Done", "Stopped", "Idle") with an icon, and a one-line description taken from the latest audit record, for example "Calling get_rent_obligation" or "Waiting for Facilities".

The **delegation map** draws a faint line between every pair of agents (full connectivity). A line only turns solid and animated when the audit trail records a delegation along it, and it stops animating when the delegated agent delivers its report. Nothing is drawn for a hand-off that did not happen.

## Resolved-ticket presentation

Two different questions are shown separately, because answering one does not answer the other.

1. **Workflow**, stored on the ticket: "Open", "Agents working", "Workflow resolved", or "Run stopped, not resolved". A ticket is marked resolved only after its agent run finishes normally. A run that fails, is blocked by a limit or times out leaves the ticket open and says so in words with a warning icon, and the ticket can be run again.
2. **Business**, derived from current records (never stored): "Needs a human decision", "Payment recorded", or "No payment involved", with a plain list of what is still outstanding (for example "Invoice 501 ($840.00) is unpaid, 3 days past due", or "Size S: 0 on hand").

If contact with the backend is lost mid-run, the page does **not** guess: it re-reads the durable records and shows either the finished result or a neutral "Connection lost; run outcome unknown" (chip "Run outcome unknown", with a "Check again" link). "Run stopped, not resolved" is used only when the backend itself confirmed a failure.

A finished ticket gets a green **RESOLVED** ink stamp and a check mark, but the card keeps its amber "Needs a human decision" chip until a person acts. A note under the board says it plainly: *resolved means the agent workflow finished, not that the business problem is solved or that anything was paid.*

Even after invoice 501 is paid, the card keeps the stock problem on its list: payment does not put a tee on the shelf, and the vendor's reprint lead time is shown. Ticket 103 needs a human decision about a discount and a 12-unit shortfall. The shop's records contain no purchase price, so the dashboard proposes no purchase and shows "no payment to approve".

## Cash display

The checking balance is the largest thing on the page: a big serif number in a frosted panel in the header, with its business date. It stays visible (pinned) on phones. When a payment is approved the number **counts down** from the old balance to the new one and a pill shows the change ("▼ −$2,400.00"). With "reduce motion" turned on the number simply changes. The balance always comes from the backend's `GET /cash`, never from a figure typed or computed in the browser.

## Approval (human in control)

- The approval panel only offers a payment when the finished run's own recommendation **asked** for one, and only for the payable linked to the ticket (invoice 501 for ticket 101, lease 1 for ticket 102).
- Every number (payee, amount, due date, billing period, cash before and after) comes from the backend's read-only `GET /payments/preview`, read from the database at that moment. Blockers (already paid, not enough cash) and cautions (for invoice 501: paying does not guarantee the shirt can ship) are shown before the person acts.
- Nothing is paid automatically. The sequence is: type your name, tick the review box, press "Approve payment of $X…", confirm in a dialog. The button stays disabled until the desk is unlocked, the name looks like a person's name, the box is ticked and the server says the payment can be approved.
- The server checks the amount, duplicates and cash again when the request arrives. An agent's recommendation is never treated as an approval.
- Running the agents and approving payments need the admin token. The "Unlock desk" field keeps it in this tab's memory only: never stored, never in a URL, never logged, never in the bundle. A reload or "Lock" forgets it.
- There is no "Reset" button in the dashboard. Resetting the database stays a protected backend operation for testing and administration.

## Distinctive design choices

- **Case files and ink stamps.** Tickets look like paper case files, and finishing one stamps it "RESOLVED". The stamp is decorative; the words and icons carry the meaning.
- **A radio log that tells the truth.** The timeline shows only what the audit trail recorded: hand-offs with the task text, tool calls with their arguments, results, and reports. Where the audit trail shortened a long text, the page says so. In real runs the agents rarely produce free text, so the log reads as hand-offs and tool use, not chat.
- **A map that only draws what happened.** Delegation lines appear because the audit says they happened.
- **Workflow and business status kept apart,** so a green check never overstates what was achieved.
- **A cash ticker that moves.** The count-down makes the consequence of a human's approval visible.
- **Palette.** Navy, light blue and white throughout, with small green and amber marks for status, each paired with an icon and words.

## Responsive layout and accessibility

- Three columns above 1180 px, two columns to 760 px, one column below, with the cash panel pinned at the top on phones. There was no horizontal scrolling at 375 px in testing.
- Everything is reachable by keyboard with visible focus rings. Dialogs trap focus, close on Escape and return focus. The timeline is an `aria-live` log; the cash amount is announced when it changes. Status always has text and an icon as well as colour. `prefers-reduced-motion` turns off the stamp, the packet animation and the count-up.

## What this design does not do

- It does not stream tokens. The audit trail records steps, so the page shows steps.
- It does not offer a purchase workflow. Only invoice and rent payments exist.
- It is a local development tool: one shared admin token, no user accounts. It is not a production financial-authorization system.
