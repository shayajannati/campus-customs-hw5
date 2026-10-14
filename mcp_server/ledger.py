"""Restricted LEDGER service (Homework 5, Problem 7): the only code that writes to the database.

This is deliberately NOT part of the agent-facing server (``server.py`` / ``queries.py``),
which stays read-only. Nothing in ``backend/agents.py`` or ``orchestrator.py`` imports or
connects to this module. The FastAPI app calls it, through ``ledger_server.py``, only after
an authenticated human request.

Three operations, and no generic SQL:

* ``record_payment``: pay one vendor invoice or one month of rent from the checking account.
* ``reset_working_database``: restore the working copy from the untouched original.
* ``mark_ticket_resolved``: record that the agent WORKFLOW for a ticket finished (open -> resolved
  only; never reverses). This is not a business resolution: payments and discounts still need a human.

Payment conventions (``payments`` columns the schema leaves undefined)
    kind = "invoice"  ref_id = the invoice id
    kind = "rent"     ref_id = lease_id * 1_000_000 + YYYYMM, where YYYYMM is the billing
                      period (the month of the lease's next_due date at payment time)
    account = "checking", paid_at = the business date from ``desk`` (not the computer clock),
    approved_by = the human who approved it.

Paths come from this file's location, so they do not depend on the working directory.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORIGINAL_DB = DATA_DIR / "campus_customs.db"  # read-only source for a reset; never written
WORKING_DB = DATA_DIR / "campus_customs_new.db"  # the only database this module may change

CHECKING = "checking"
KIND_INVOICE = "invoice"
KIND_RENT = "rent"
RENT_REF_MULTIPLIER = 1_000_000
TARGET_TYPES = (KIND_INVOICE, KIND_RENT)
_FORBIDDEN_APPROVERS = {
    "boss", "inventory", "accounting", "facilities", "customer_service", "customer service",
    "agent", "ai", "system", "bot", "assistant", "llm", "admin", "root", "human", "user",
}
_APPROVER_RE = re.compile(r"^[A-Za-z][A-Za-z .,'\-]{0,78}[A-Za-z.]$")
BUSY_TIMEOUT_S = 5


class Rejected(Exception):
    """A business-rule refusal. Nothing was changed."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cents(value: Any, field: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Rejected("invalid_input", f"'{field}' is not a number.") from None
    if not d.is_finite():
        raise Rejected("invalid_input", f"'{field}' must be a finite number.")
    if d != d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
        raise Rejected("invalid_input", f"'{field}' must have at most 2 decimal places.")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def rent_ref_id(lease_id: int, due_date: str) -> tuple[int, str]:
    """(ref_id, 'YYYY-MM') for a lease's rent due on ``due_date``."""
    m = re.match(r"^(\d{4})-(\d{2})-\d{2}$", due_date or "")
    try:
        date.fromisoformat(due_date or "")  # a real calendar date, not just a pattern
    except ValueError:
        m = None
    if not m:
        raise Rejected("data_error", f"Lease {lease_id} has an unreadable next_due date.")
    return lease_id * RENT_REF_MULTIPLIER + int(m.group(1)) * 100 + int(m.group(2)), f"{m.group(1)}-{m.group(2)}"


def _clean_approver(name: Any) -> str:
    if not isinstance(name, str):
        raise Rejected("invalid_input", "'approved_by' must be text.")
    name = " ".join(name.split())
    if name.lower() in _FORBIDDEN_APPROVERS or not _APPROVER_RE.match(name):
        raise Rejected(
            "invalid_input",
            "'approved_by' must be the name of a human approver (2-80 letters), not an agent or role.",
        )
    return name


def _refuse_if_original(path: Path) -> None:
    if path.exists() and ORIGINAL_DB.exists() and os.path.samefile(path, ORIGINAL_DB):
        raise Rejected("forbidden_target", "Refusing to write to the original database.")


def _connect_rw() -> sqlite3.Connection:
    if not WORKING_DB.is_file():
        raise Rejected("database_missing", "The working database does not exist.")
    _refuse_if_original(WORKING_DB)
    conn = sqlite3.connect(WORKING_DB, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --------------------------------------------------------------------------- #
# the single-purpose write steps (kept separate so tests can force failures)
# --------------------------------------------------------------------------- #
def _insert_payment(conn: sqlite3.Connection, kind: str, ref_id: int, amount: Decimal, paid_at: str, approver: str) -> int:
    cur = conn.execute(
        "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, ref_id, float(amount), CHECKING, paid_at, approver),
    )
    return int(cur.lastrowid)


def _debit_checking(conn: sqlite3.Connection, new_balance: Decimal, old_balance: Decimal) -> None:
    cur = conn.execute(
        "UPDATE cash_accounts SET balance = ? WHERE name = ? AND balance = ?",
        (float(new_balance), CHECKING, float(old_balance)),
    )
    if cur.rowcount != 1:
        raise RuntimeError("Checking balance changed during the payment; rolled back.")


def _mark_invoice_paid(conn: sqlite3.Connection, invoice_id: int) -> None:
    cur = conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ? AND status = 'open'", (invoice_id,))
    if cur.rowcount != 1:
        raise RuntimeError("Invoice was not open; rolled back.")


# --------------------------------------------------------------------------- #
# Operation 1: record an approved payment
# --------------------------------------------------------------------------- #
def record_payment(target_type: str, target_id: int, expected_amount: Any, approved_by: str) -> dict[str, Any]:
    """Pay an invoice or one month of rent. All-or-nothing in one SQLite transaction."""
    if target_type not in TARGET_TYPES:
        raise Rejected("invalid_input", f"'target_type' must be one of {list(TARGET_TYPES)}.")
    if isinstance(target_id, bool) or not isinstance(target_id, int) or target_id < 1:
        raise Rejected("invalid_input", "'target_id' must be a positive whole number.")
    expected = _cents(expected_amount, "expected_amount")
    if expected <= 0:
        raise Rejected("invalid_input", "'expected_amount' must be greater than zero.")
    approver = _clean_approver(approved_by)

    conn = _connect_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")  # take the write lock up front
        try:
            row = conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
            if row is None:
                raise Rejected("data_error", "The desk table has no business date.")
            business_date = row["date_today"]

            if target_type == KIND_INVOICE:
                inv = conn.execute("SELECT id, amount, status, due_date FROM invoices WHERE id = ?", (target_id,)).fetchone()
                if inv is None:
                    raise Rejected("not_found", f"No invoice {target_id}.")
                kind, ref_id, period = KIND_INVOICE, target_id, None
                amount = _cents(inv["amount"], "invoice amount")
            else:
                lease = conn.execute("SELECT id, monthly_rent, next_due FROM leases WHERE id = ?", (target_id,)).fetchone()
                if lease is None:
                    raise Rejected("not_found", f"No lease {target_id}.")
                ref_id, period = rent_ref_id(target_id, lease["next_due"])
                kind = KIND_RENT
                amount = _cents(lease["monthly_rent"], "monthly rent")

            dup = conn.execute("SELECT id FROM payments WHERE kind = ? AND ref_id = ?", (kind, ref_id)).fetchone()
            if dup is not None:
                raise Rejected("duplicate", f"A {kind} payment for this {'billing period' if period else 'invoice'} already exists.", existing_payment_id=dup["id"], billing_period=period)
            if target_type == KIND_INVOICE and inv["status"] != "open":
                raise Rejected("not_payable", f"Invoice {target_id} has status '{inv['status']}', not 'open'.")
            if amount <= 0:
                raise Rejected("data_error", "The recorded amount is not positive.")
            if amount != expected:
                raise Rejected("amount_mismatch", "expected_amount does not match the amount recorded in the database.", database_amount=float(amount))

            acct = conn.execute("SELECT balance FROM cash_accounts WHERE name = ?", (CHECKING,)).fetchone()
            if acct is None:
                raise Rejected("data_error", "There is no checking account.")
            balance = _cents(acct["balance"], "balance")
            if balance - amount < 0:
                raise Rejected("insufficient_funds", "The checking balance does not cover this payment.", balance=float(balance), amount=float(amount))

            payment_id = _insert_payment(conn, kind, ref_id, amount, business_date, approver)
            _debit_checking(conn, balance - amount, balance)
            if target_type == KIND_INVOICE:
                _mark_invoice_paid(conn, target_id)
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.OperationalError as exc:
        raise Rejected("busy", f"The database is busy or locked: {exc}") from exc
    finally:
        conn.close()

    return {
        "payment_id": payment_id,
        "kind": kind,
        "ref_id": ref_id,
        "target_type": target_type,
        "target_id": target_id,
        "billing_period": period,
        "amount": float(amount),
        "account": CHECKING,
        "paid_at": business_date,
        "approved_by": approver,
        "balance_before": float(balance),
        "balance_after": float(balance - amount),
        "invoice_status": "paid" if target_type == KIND_INVOICE else None,
    }


# --------------------------------------------------------------------------- #
# Operation 2: reset the working database from the original
# --------------------------------------------------------------------------- #
def _check_sqlite_file(path: Path) -> list[str]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise Rejected("integrity_failed", f"{path.name} failed the SQLite integrity check.")
        return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
    finally:
        conn.close()


def reset_working_database() -> dict[str, Any]:
    """Replace the working database with a byte-identical copy of the original. Never writes the original."""
    if not ORIGINAL_DB.is_file():
        raise Rejected("database_missing", "The original database is missing; nothing was changed.")
    _refuse_if_original(WORKING_DB)
    before = _sha256(ORIGINAL_DB)
    tables = _check_sqlite_file(ORIGINAL_DB)

    if WORKING_DB.is_file():  # prove nobody holds a lock before replacing the file
        probe = sqlite3.connect(WORKING_DB, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            probe.execute("BEGIN EXCLUSIVE")
            probe.execute("ROLLBACK")
        except sqlite3.OperationalError as exc:
            raise Rejected("busy", f"The working database is in use: {exc}") from exc
        finally:
            probe.close()

    fd, tmp_name = tempfile.mkstemp(dir=DATA_DIR, prefix=".reset-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, open(ORIGINAL_DB, "rb") as src:  # original opened read-only
            for chunk in iter(lambda: src.read(1 << 20), b""):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if _sha256(tmp) != before:
            raise Rejected("copy_failed", "The copy did not match the original; nothing was replaced.")
        if _check_sqlite_file(tmp) != tables:
            raise Rejected("copy_failed", "The copy has different tables from the original; nothing was replaced.")
        os.chmod(tmp, 0o600)
        os.replace(tmp, WORKING_DB)  # atomic swap
    finally:
        if tmp.exists():
            tmp.unlink()
    for suffix in ("-journal", "-wal", "-shm"):
        side = Path(str(WORKING_DB) + suffix)
        if side.exists():
            side.unlink()

    after = _sha256(ORIGINAL_DB)
    if after != before:  # cannot happen; checked anyway
        raise RuntimeError("The original database changed during reset.")
    return {
        "restored": True,
        "original_sha256_before": before,
        "original_sha256_after": after,
        "working_sha256": _sha256(WORKING_DB),
        "tables": tables,
    }


# --------------------------------------------------------------------------- #
# Operation 3: mark a ticket's workflow as finished (open -> resolved, one way)
# --------------------------------------------------------------------------- #
def mark_ticket_resolved(ticket_id: int) -> dict[str, Any]:
    """Set tickets.status to 'resolved' if it is 'open'. Idempotent. Touches nothing else."""
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id < 1:
        raise Rejected("invalid_input", "'ticket_id' must be a positive whole number.")
    conn = _connect_rw()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if row is None:
                raise Rejected("not_found", f"No ticket {ticket_id}.")
            before = row["status"]
            if before == "open":
                cur = conn.execute("UPDATE tickets SET status = 'resolved' WHERE id = ? AND status = 'open'", (ticket_id,))
                if cur.rowcount != 1:
                    raise RuntimeError("Ticket status changed during the update; rolled back.")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.OperationalError as exc:
        raise Rejected("busy", f"The database is busy or locked: {exc}") from exc
    finally:
        conn.close()
    return {"ticket_id": ticket_id, "status_before": before, "status_after": "resolved" if before == "open" else before, "changed": before == "open"}
