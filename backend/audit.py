"""Append-only audit trail: output/audit_trail.json.

Format: one valid JSON array with one object per agent-loop step.

Durable append: a record is added by replacing ONLY the closing "]" at the end of
the file with ",\\n<record>\\n]". Earlier bytes are never rewritten, reordered or
deleted, and the file is valid JSON after every write. An exclusive file lock stops
concurrent writers interleaving.

Fail safe: if the file exists but is not a valid JSON array, nothing is written,
the file is left exactly as it is, and ``AuditCorruptError`` is raised, which stops
the agent run. The file is never renamed, reset or replaced.

Never recorded: API keys, bearer tokens, e-mail addresses. Long free text is
shortened.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import AuditRecord

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "output" / "audit_trail.json"

_SECRETS = [
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}"), "[redacted-key]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "[redacted-token]"),
    (re.compile(r"(?i)(portkey[_-]?api[_-]?key|x-portkey-api-key)\s*[=:]\s*\S+"), "[redacted-key]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[redacted-email]"),
]


SAFE_AUDIT_MESSAGE = "The audit trail file is unreadable. It was left unchanged; fix or move it by hand."


class AuditCorruptError(RuntimeError):
    """The audit file exists but is not a valid JSON array. It has been left untouched.

    ``str(exc)`` is a fixed, user-safe sentence (it is shown in API responses and the dashboard).
    The detailed reason, including the file path, is in ``exc.diagnostic`` and belongs in server logs only.
    """

    def __init__(self, diagnostic: str) -> None:
        super().__init__(SAFE_AUDIT_MESSAGE)
        self.diagnostic = diagnostic


def redact(text: str) -> str:
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


def shorten(value: Any, limit: int) -> Any:
    """Make a value JSON-safe, redacted and no longer than ``limit`` characters."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str, ensure_ascii=False)
    elif not isinstance(value, (str, int, float, bool)) and value is not None:
        value = str(value)
    if isinstance(value, str):
        value = redact(value)
        if len(value) > limit:
            value = value[:limit] + f"… [+{len(value) - limit} chars]"
    return value


class AuditTrail:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_PATH

    # -- reading -------------------------------------------------------------
    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return self._parse(self.path.read_bytes())

    def _parse(self, raw: bytes) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuditCorruptError(
                f"{self.path} is not valid JSON ({exc}). It was left unchanged; fix or move it by hand."
            ) from exc
        if not isinstance(data, list) or not raw.rstrip().endswith(b"]"):
            raise AuditCorruptError(
                f"{self.path} is not a JSON array. It was left unchanged; fix or move it by hand."
            )
        return data

    # -- writing -------------------------------------------------------------
    def append(self, record: AuditRecord | dict[str, Any]) -> None:
        payload = record.model_dump(mode="json") if isinstance(record, AuditRecord) else record
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        text = "\n".join("  " + line for line in text.splitlines())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # "a+b" creates the file if missing but never truncates; we seek before reading.
        with open(self.path, "a+b") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                if not raw.strip():
                    # A brand-new (or zero-byte) file: start the array.
                    fh.seek(0)
                    fh.write(("[\n" + text + "\n]\n").encode("utf-8"))
                    fh.flush()
                    os.fsync(fh.fileno())
                    return
                existing = self._parse(raw)  # raises, and writes nothing, if damaged
                end = len(raw.rstrip())  # index just after the final "]"
                prefix = raw[: end - 1].rstrip()  # everything before the closing "]"
                sep = b"\n" if not existing else b",\n"
                tail_ws = raw[end:]  # keep any trailing newline exactly as it was
                new_bytes = sep + text.encode("utf-8") + b"\n]" + tail_ws
                # Only the closing "]" region changes: write from the end of `prefix`.
                with open(self.path, "r+b") as rw:
                    rw.seek(len(prefix))
                    rw.write(new_bytes)
                    rw.truncate()
                    rw.flush()
                    os.fsync(rw.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
