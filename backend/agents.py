"""The Campus Customs agent team: Boss, Inventory, Accounting, Facilities, Customer Service.

* Built with PydanticAI. Every agent uses the one model ``gpt-6-luna`` through Portkey.
* Every shop fact comes from the MCP server (``mcp_server/server.py``) via an MCP
  client. This package has no SQLite code and does not open the database.
* Agents can hand work to each other with the ``delegate`` tool (full connectivity),
  but never back to an agent already in the chain, and within shared limits.
* Every agent-loop step is appended to ``output/audit_trail.json`` (see ``audit.py``).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp.client.transports import StdioTransport
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage, UsageLimits

from audit import new_id, redact, shorten, utc_now
from models import (
    AGENT_NAMES,
    AgentName,
    AgentReport,
    AuditRecord,
    RunDeps,
    RunLimits,
    RunState,
    TicketDecision,
)

log = logging.getLogger("campus_customs")

ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MCP_SERVER_SCRIPT = ROOT / "mcp_server" / "server.py"

# Exactly this model, for every agent. Not overridable by an environment variable.
MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
MODEL_TIMEOUT_S = 60

# Which MCP tools each role may see (least privilege). All of them are read-only.
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "boss": frozenset({"get_business_date", "list_open_tickets", "get_ticket_context"}),
    "inventory": frozenset(
        {"get_business_date", "get_ticket_context", "check_product_stock", "evaluate_bulk_order", "get_vendor_info"}
    ),
    "accounting": frozenset(
        {
            "get_business_date",
            "get_ticket_context",
            "get_invoice_status",
            "get_payment_history",
            "get_cash_position",
            "get_vendor_info",
            "evaluate_bulk_order",
        }
    ),
    "facilities": frozenset(
        {"get_business_date", "get_ticket_context", "get_rent_obligation", "get_cash_position", "get_payment_history"}
    ),
    # Customer Service gets only tools that the server itself keeps free of unit cost,
    # margins, invoices, vendors, leases and cash. It has no get_ticket_context.
    "customer_service": frozenset(
        {"get_business_date", "get_customer_safe_ticket_context", "check_product_stock"}
    ),
}

ROLE_OUTPUT: dict[str, type] = {
    "boss": TicketDecision,
    "inventory": AgentReport,
    "accounting": AgentReport,
    "facilities": AgentReport,
    "customer_service": AgentReport,
}


# --------------------------------------------------------------------------- #
# Model and MCP connection
# --------------------------------------------------------------------------- #
def load_env() -> None:
    """Load settings from .env files without overriding variables already set in the environment.

    Order of precedence: the real environment, then ``<repository>/.env`` (the documented location,
    copy ``.env.example``), then ``<repository>/../.env`` (the older layout, kept as a fallback).
    Values are never printed or logged.
    """
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT.parent / ".env")


def build_model() -> Model:
    """The one real model: gpt-6-luna via Portkey. Needs PORTKEY_API_KEY (see .env.example)."""
    load_env()
    api_key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("PORTKEY_API_KEY is not set. Copy .env.example to .env in the repository root and fill it in.")
    client = AsyncOpenAI(
        api_key="portkey",  # unused placeholder; Portkey authenticates with the header below
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-api-key": api_key},
        timeout=MODEL_TIMEOUT_S,
        max_retries=0,  # never silently re-send a failed request
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def build_mcp_toolset() -> MCPToolset:
    """Connect to the Campus Customs MCP server over stdio (the only route to shop data)."""
    transport = StdioTransport(
        command=sys.executable,
        args=[str(MCP_SERVER_SCRIPT)],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        keep_alive=False,  # FastMCP defaults to True, which leaves the server process running after the run
    )
    return MCPToolset(transport, id="campus-customs")


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Audit helpers
# --------------------------------------------------------------------------- #
def _usage_dict(usage: RunUsage) -> dict[str, int]:
    return {
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def record(
    deps: RunDeps,
    event: str,
    details: dict[str, Any],
    usage: RunUsage | None = None,
    model: str | None = None,
) -> None:
    """Append one record to the audit trail. Raises (and so stops the run) if the trail is damaged."""
    st = deps.state
    st.step += 1
    limit = st.limits.max_text_chars_in_audit
    clean = {k: shorten(v, limit) for k, v in details.items()}
    st.audit.append(
        AuditRecord(
            audit_id=new_id(),
            timestamp_utc=utc_now(),
            run_id=st.run_id,
            ticket_id=st.ticket_id,
            step=st.step,
            agent=deps.agent,
            parent_agent=deps.parent,
            depth=len(deps.chain) - 1,
            event=event,  # type: ignore[arg-type]
            details=clean,
            usage=_usage_dict(usage) if usage else {},
            model=model,
            run_mode=st.run_mode,
        )
    )


def usage_limits(limits: RunLimits) -> UsageLimits:
    return UsageLimits(
        request_limit=limits.request_limit,
        tool_calls_limit=limits.tool_calls_limit,
        total_tokens_limit=limits.total_tokens_limit,
    )


async def run_logged(agent: Agent[RunDeps, Any], prompt: str, deps: RunDeps, usage: RunUsage | None = None) -> Any:
    """Run one agent loop and audit every step (prompt, model text, tool calls and returns, output)."""
    st = deps.state
    model_name = getattr(agent.model, "model_name", None) if agent.model else None
    record(deps, "run_started", {"prompt": prompt})
    try:
        async with agent.iter(
            prompt, deps=deps, usage=usage, usage_limits=usage_limits(st.limits)
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    for part in node.request.parts:
                        if isinstance(part, UserPromptPart):
                            record(deps, "user_prompt", {"content": part.content}, run.usage, model_name)
                        elif isinstance(part, ToolReturnPart):
                            record(
                                deps,
                                "tool_return",
                                {"tool": part.tool_name, "result": part.content},
                                run.usage,
                                model_name,
                            )
                        elif isinstance(part, RetryPromptPart):
                            record(deps, "retry", {"tool": part.tool_name, "message": part.content}, run.usage, model_name)
                elif Agent.is_call_tools_node(node):
                    for part in node.model_response.parts:
                        if isinstance(part, TextPart):
                            record(deps, "model_text", {"text": part.content}, run.usage, model_name)
                        elif isinstance(part, ToolCallPart):
                            record(
                                deps,
                                "tool_call",
                                {"tool": part.tool_name, "arguments": part.args},
                                run.usage,
                                model_name,
                            )
            result = run.result
        assert result is not None
        record(deps, "final_output", {"output": result.output.model_dump_json()}, run.usage, model_name)
        return result
    except Exception as exc:  # noqa: BLE001 - audited, then re-raised
        # Audit-trail failures must not be hidden behind another audit write.
        from audit import AuditCorruptError

        if isinstance(exc, AuditCorruptError):
            raise
        log.error("agent step failed in %s (ticket %s): %s: %s", deps.agent, st.ticket_id, type(exc).__name__, redact(str(exc))[:500])
        record(deps, "error", {"type": type(exc).__name__, "message": str(exc)})
        raise


# --------------------------------------------------------------------------- #
# Delegation (full connectivity, with guards)
# --------------------------------------------------------------------------- #
def _refusal(deps: RunDeps, to: str) -> str | None:
    st = deps.state
    if to not in AGENT_NAMES:
        return f"unknown agent '{to}'"
    if to in deps.chain:
        return f"'{to}' is already in the delegation chain {' -> '.join(deps.chain)}; no loops"
    if len(deps.chain) >= st.limits.max_chain_length:
        return f"maximum delegation chain length ({st.limits.max_chain_length}) reached"
    if st.delegations_used >= st.limits.max_delegations:
        return f"maximum delegations per ticket ({st.limits.max_delegations}) reached"
    return None


async def delegate(ctx: RunContext[RunDeps], to: AgentName, task: str) -> str:
    """Hand a task to another agent and get its structured report back.

    `to` is one of: boss, inventory, accounting, facilities, customer_service.
    Give a short, self-contained `task` with the ticket id and the facts they need.
    You cannot delegate to an agent already in your chain, and delegation is
    limited per ticket. The reply is the other agent's JSON report, or a line
    starting with REFUSED explaining why the delegation did not happen.
    """
    deps = ctx.deps
    reason = _refusal(deps, to)
    if reason:
        record(deps, "delegation_refused", {"to": to, "task": task, "reason": reason}, ctx.usage)
        return f"REFUSED: {reason}. Work with the facts you already have and list what is missing."
    deps.state.delegations_used += 1
    record(deps, "delegation", {"to": to, "task": task, "delegation_number": deps.state.delegations_used}, ctx.usage)
    child = deps.state.team[to]
    child_deps = replace(deps, chain=deps.chain + (to,))
    prompt = f"Ticket {deps.state.ticket_id}. Task from {deps.agent}: {task}"
    result = await run_logged(child, prompt, child_deps, usage=ctx.usage)
    deps.state.reports.append(
        {"agent": to, "delegated_by": deps.agent, "task": task, "report": result.output.model_dump(mode="json")}
    )
    return result.output.model_dump_json()


# --------------------------------------------------------------------------- #
# Building the team
# --------------------------------------------------------------------------- #
def build_team(
    toolset: Any,
    model_for: Callable[[str], Model] | None = None,
    limits: RunLimits | None = None,
) -> dict[str, Agent[RunDeps, Any]]:
    """Create the five agents. ``model_for`` lets tests inject stub models; real runs use gpt-6-luna."""
    limits = limits or RunLimits()
    real_model = None if model_for else build_model()
    team: dict[str, Agent[RunDeps, Any]] = {}
    for name in AGENT_NAMES:
        allowed = ROLE_TOOLS[name]
        agent: Agent[RunDeps, Any] = Agent(
            model_for(name) if model_for else real_model,
            deps_type=RunDeps,
            output_type=ROLE_OUTPUT[name],
            instructions=load_prompt(name),
            toolsets=[toolset.filtered(lambda ctx, tool, allowed=allowed: tool.name in allowed)],
            # No temperature: reasoning models can reject it (the Homework 4 agent doesn't set one).
            model_settings=ModelSettings(max_tokens=limits.max_output_tokens_per_request),
            name=name,
            retries=1,
        )
        agent.tool(delegate)
        team[name] = agent
    return team
