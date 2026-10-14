"""Short-lived MCP clients the API uses. This is the only way the backend touches shop data.

* ``call_read_tool``: the read-only agent-facing server (``mcp_server/server.py``).
* ``call_ledger_tool``: the restricted ledger server (``mcp_server/ledger_server.py``).
  Only the authenticated payment and reset routes in ``main.py`` may use it. It is never
  given to a PydanticAI agent (``agents.py`` and ``orchestrator.py`` do not import this module).

Each call starts the server as a child process and closes it afterwards, so no database
connection stays open between requests (important before the database file is replaced).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parent.parent
READ_SERVER = ROOT / "mcp_server" / "server.py"
LEDGER_SERVER = ROOT / "mcp_server" / "ledger_server.py"


def _client(script: Path) -> Client:
    return Client(
        StdioTransport(command=sys.executable, args=[str(script)], env={"PYTHONDONTWRITEBYTECODE": "1"}, keep_alive=False)
    )


async def _call(script: Path, name: str, args: dict[str, Any]) -> Any:
    async with _client(script) as client:
        result = await client.call_tool(name, args)
        return result.data


async def call_read_tool(name: str, args: dict[str, Any] | None = None) -> Any:
    return await _call(READ_SERVER, name, args or {})


async def call_ledger_tool(name: str, args: dict[str, Any]) -> Any:
    if name not in {"record_approved_payment", "reset_working_database", "mark_ticket_resolved"}:
        raise ValueError(f"Unknown ledger tool: {name}")
    return await _call(LEDGER_SERVER, name, args)
