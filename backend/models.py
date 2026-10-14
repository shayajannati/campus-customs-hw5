"""Data types for the Campus Customs agent team (Homework 5, Problem 5).

Everything the agents return, the limits they run under, and the shape of the
audit records is defined here. The agents themselves live in ``agents.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    from pydantic_ai import Agent

    from audit import AuditTrail

AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
AGENT_NAMES: tuple[str, ...] = ("boss", "inventory", "accounting", "facilities", "customer_service")

EscalationCategory = Literal[
    "payment", "discount", "stock_commitment", "customer_promise", "suspicious_request", "other"
]


# --------------------------------------------------------------------------- #
# What agents return
# --------------------------------------------------------------------------- #
class Fact(BaseModel):
    """One fact and the MCP tool that returned it."""

    statement: str
    source_tool: str = Field(description="Name of the MCP tool whose result contains this fact.")


class EscalationRequest(BaseModel):
    """A decision only a human may make. Agents ask. They never decide."""

    category: EscalationCategory
    decision_needed: str
    reason: str
    recommendation: str
    requires_human_approval: Literal[True] = True


class AgentReport(BaseModel):
    """What every specialist agent returns to whoever delegated to it."""

    agent: AgentName
    summary: str
    facts: list[Fact] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    escalations: list[EscalationRequest] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    customer_draft: str | None = Field(
        default=None, description="Customer Service only: a reply draft for a human to review."
    )


class TicketDecision(BaseModel):
    """The Boss's final recommendation for one ticket."""

    ticket_id: int
    status: Literal["recommended", "needs_human_approval", "blocked"]
    summary: str
    facts: list[Fact] = Field(default_factory=list)
    actions_recommended: list[str] = Field(default_factory=list)
    escalations: list[EscalationRequest] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    customer_draft: str | None = None
    specialists_consulted: list[AgentName] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Limits (token and request use, delegation depth)
# --------------------------------------------------------------------------- #
class RunLimits(BaseModel):
    """Hard caps for one ticket. The whole agent team shares them."""

    request_limit: int = 16  # model calls across all agents
    tool_calls_limit: int = 20  # MCP and delegate calls across all agents
    total_tokens_limit: int = 60_000  # input + output tokens across all agents
    max_output_tokens_per_request: int = 2_500
    max_chain_length: int = 3  # e.g. boss -> inventory -> accounting
    max_delegations: int = 4  # delegate calls per ticket
    max_text_chars_in_audit: int = 400


# --------------------------------------------------------------------------- #
# Run-time state passed to every agent as `deps`
# --------------------------------------------------------------------------- #
@dataclass
class RunState:
    """Mutable state shared by every agent working on one ticket."""

    run_id: str
    ticket_id: int
    limits: RunLimits
    audit: "AuditTrail"
    run_mode: Literal["live", "stub"] = "live"
    team: dict[str, "Agent[RunDeps, Any]"] = field(default_factory=dict)
    delegations_used: int = 0
    step: int = 0
    # Problem 8: each delegated specialist's finished report, collected so the dashboard can show it.
    reports: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RunDeps:
    """Per-agent view: shared state plus where this agent sits in the delegation chain."""

    state: RunState
    chain: tuple[str, ...]  # agents currently waiting on this one, ending with this agent

    @property
    def agent(self) -> str:
        return self.chain[-1]

    @property
    def parent(self) -> str | None:
        return self.chain[-2] if len(self.chain) > 1 else None


# --------------------------------------------------------------------------- #
# Audit record (one per agent-loop step)
# --------------------------------------------------------------------------- #
class AuditRecord(BaseModel):
    audit_id: str
    timestamp_utc: str
    run_id: str
    ticket_id: int | None  # None for human actions that are not about one ticket (payments, resets)
    step: int
    agent: str
    parent_agent: str | None
    depth: int
    event: Literal[
        "run_started",
        "user_prompt",
        "model_text",
        "tool_call",
        "tool_return",
        "retry",
        "delegation",
        "delegation_refused",
        "final_output",
        "error",
        # Problem 7: human-authorized actions made through the API (agent "human")
        "payment_attempt",
        "payment_committed",
        "payment_rejected",
        "payment_error",
        "reset_attempt",
        "reset_committed",
        "reset_rejected",
        "reset_error",
        # Problem 8: written by the API (agent "system") around an asynchronous run
        "run_summary",
        "ticket_resolved",
        "ticket_resolve_failed",
    ]
    details: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
    model: str | None = None
    # "live" = real gpt-6-luna calls, "stub" = scripted test model. The first 88 records in
    # the trail predate this field; they are the stub runs and have model names "stub-...".
    run_mode: Literal["live", "stub"] | None = None


# --------------------------------------------------------------------------- #
# Problem 7: API request and response types (backend/main.py)
# --------------------------------------------------------------------------- #
from decimal import Decimal  # noqa: E402

from pydantic import ConfigDict, condecimal  # noqa: E402


class Payable(BaseModel):
    target_type: Literal["invoice", "rent"]
    target_id: int


class TicketBusiness(BaseModel):
    """Business state, derived from current records. Separate from the workflow state."""

    state: Literal["paid", "awaiting_human_decision", "no_payment_applicable"]
    payable: Payable | None = None
    pending: list[str] = Field(default_factory=list)
    operational_issue: bool = False


class TicketSummary(BaseModel):
    ticket_id: int
    type: str
    subject: str
    sku: str | None = None
    size: str | None = None
    qty: int | None = None
    lease_id: int | None = None
    invoice_id: int | None = None
    state: Literal["open", "resolved"]
    raw_status: str
    created_at: str
    business: TicketBusiness | None = None


class TicketsResponse(BaseModel):
    business_date: str | None
    count: int
    tickets: list[TicketSummary]


class UsageSummary(BaseModel):
    requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RunResponse(BaseModel):
    run_id: str
    ticket_id: int
    run_mode: Literal["live", "stub"]
    decision: TicketDecision
    usage: UsageSummary


class EventsResponse(BaseModel):
    total_matching: int
    returned: int
    events: list[dict[str, Any]]


class CashResponse(BaseModel):
    account: Literal["checking"]
    balance: float
    as_of: str


class PaymentApprovalRequest(BaseModel):
    """Human confirmation fields. These are NOT authentication: the admin token is."""

    model_config = ConfigDict(extra="forbid")

    target_type: Literal["invoice", "rent"]
    target_id: int = Field(ge=1, le=1_000_000)
    expected_amount: condecimal(gt=Decimal(0), max_digits=10, decimal_places=2)  # type: ignore[valid-type]
    approved_by: str = Field(min_length=2, max_length=80)
    confirm: Literal[True]


class PaymentApprovalResponse(BaseModel):
    payment_id: int
    kind: str
    ref_id: int
    target_type: Literal["invoice", "rent"]
    target_id: int
    billing_period: str | None
    amount: float
    paid_at: str
    approved_by: str
    balance_before: float
    balance_after: float
    invoice_status: str | None
    approval_id: str


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class ResetResponse(BaseModel):
    restored: bool
    original_sha256_before: str
    original_sha256_after: str
    working_sha256: str
    approval_id: str


class ErrorResponse(BaseModel):
    code: str
    detail: str


# --------------------------------------------------------------------------- #
# Problem 8: asynchronous runs, run summaries and payment preview
# --------------------------------------------------------------------------- #
class RunStartResponse(BaseModel):
    run_id: str
    ticket_id: int
    status: Literal["running"]
    run_mode: Literal["live", "stub"]


class SpecialistReport(BaseModel):
    agent: str
    delegated_by: str
    task: str
    report: AgentReport


class RunStatusResponse(BaseModel):
    run_id: str
    ticket_id: int
    status: Literal["running", "finished", "failed"]
    run_mode: Literal["live", "stub"]
    started_at: str
    finished_at: str | None = None
    decision: TicketDecision | None = None
    agent_reports: list[SpecialistReport] = Field(default_factory=list)
    usage: UsageSummary | None = None
    resolved: bool = False  # the ticket's workflow was marked resolved (NOT the business issue)
    note: str | None = None  # why a run failed, or why a finished run could not be marked resolved


class TicketDecisionResponse(BaseModel):
    ticket_id: int
    run_id: str
    outcome: Literal["finished", "blocked"]
    finished_at: str
    decision: TicketDecision
    agent_reports: list[SpecialistReport] = Field(default_factory=list)


class PaymentBlocker(BaseModel):
    code: str
    message: str


class PaymentPreview(BaseModel):
    """Everything shown to the human before approving, read from the database at request time."""

    target_type: Literal["invoice", "rent"]
    target_id: int
    payee: str
    description: str | None = None
    amount: float
    amount_text: str  # exact value to send back as expected_amount
    due_date: str | None = None
    days_until_due: int | None = None  # negative when past due
    billing_period: str | None = None
    already_paid: bool
    existing_payment_id: int | None = None
    cash_before: float
    cash_after: float
    can_approve: bool
    blockers: list[PaymentBlocker] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    business_date: str | None = None
