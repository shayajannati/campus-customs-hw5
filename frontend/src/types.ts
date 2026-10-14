// Types mirror the backend's Pydantic models (backend/models.py). Field names are the API's.

export type AgentId = 'boss' | 'inventory' | 'accounting' | 'facilities' | 'customer_service'

export type TicketState = 'open' | 'resolved'
export type BusinessState = 'paid' | 'awaiting_human_decision' | 'no_payment_applicable'

export interface Payable {
  target_type: 'invoice' | 'rent'
  target_id: number
}

export interface TicketBusiness {
  state: BusinessState
  payable: Payable | null
  pending: string[]
  operational_issue: boolean
}

export interface Ticket {
  ticket_id: number
  type: string
  subject: string
  sku: string | null
  size: string | null
  qty: number | null
  lease_id: number | null
  invoice_id: number | null
  state: TicketState
  raw_status: string
  created_at: string
  business: TicketBusiness | null
}

export interface TicketsResponse {
  business_date: string | null
  count: number
  tickets: Ticket[]
}

export interface CashResponse {
  account: 'checking'
  balance: number
  as_of: string
}

export interface AuditEvent {
  audit_id: string
  timestamp_utc: string
  run_id: string
  ticket_id: number | null
  step: number
  agent: string
  parent_agent: string | null
  depth: number
  event: string
  details: Record<string, string | number | boolean | null>
  usage: Record<string, number>
  model: string | null
  run_mode: 'live' | 'stub' | null
}

export interface EventsResponse {
  total_matching: number
  returned: number
  events: AuditEvent[]
}

export interface Fact {
  statement: string
  source_tool: string
}

export interface Escalation {
  category: string
  decision_needed: string
  reason: string
  recommendation: string
  requires_human_approval: true
}

export interface AgentReport {
  agent: string
  summary: string
  facts: Fact[]
  recommendations: string[]
  escalations: Escalation[]
  open_questions: string[]
  risks: string[]
  customer_draft: string | null
}

export interface TicketDecision {
  ticket_id: number
  status: 'recommended' | 'needs_human_approval' | 'blocked'
  summary: string
  facts: Fact[]
  actions_recommended: string[]
  escalations: Escalation[]
  risks: string[]
  customer_draft: string | null
  specialists_consulted: string[]
}

export interface SpecialistReport {
  agent: string
  delegated_by: string
  task: string
  report: AgentReport
}

export interface Usage {
  requests: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
}

export interface RunStart {
  run_id: string
  ticket_id: number
  status: 'running'
  run_mode: 'live' | 'stub'
}

export interface RunStatus {
  run_id: string
  ticket_id: number
  status: 'running' | 'finished' | 'failed'
  run_mode: 'live' | 'stub'
  started_at: string
  finished_at: string | null
  decision: TicketDecision | null
  agent_reports: SpecialistReport[]
  usage: Usage | null
  resolved: boolean
  note: string | null
}

export interface TicketDecisionResponse {
  ticket_id: number
  run_id: string
  outcome: 'finished' | 'blocked'
  finished_at: string
  decision: TicketDecision
  agent_reports: SpecialistReport[]
}

export interface PaymentBlocker {
  code: string
  message: string
}

export interface PaymentPreview {
  target_type: 'invoice' | 'rent'
  target_id: number
  payee: string
  description: string | null
  amount: number
  amount_text: string
  due_date: string | null
  days_until_due: number | null
  billing_period: string | null
  already_paid: boolean
  existing_payment_id: number | null
  cash_before: number
  cash_after: number
  can_approve: boolean
  blockers: PaymentBlocker[]
  caveats: string[]
  business_date: string | null
}

export interface PaymentReceipt {
  payment_id: number
  kind: string
  ref_id: number
  target_type: 'invoice' | 'rent'
  target_id: number
  billing_period: string | null
  amount: number
  paid_at: string
  approved_by: string
  balance_before: number
  balance_after: number
  invoice_status: string | null
  approval_id: string
}

/** What the page shows for one ticket's run. Built only from API and audit data. */
export interface RunView {
  runId: string
  ticketId: number
  /** 'unknown' = contact was lost and the outcome could not be confirmed (never shown as a failure). */
  status: 'running' | 'finished' | 'failed' | 'unknown'
  events: AuditEvent[]
  result: RunStatus | null
  /** Why the run is failed/unknown when no backend result is available. */
  note: string | null
}
