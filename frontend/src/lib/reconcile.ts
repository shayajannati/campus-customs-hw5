import type { Ticket, TicketDecisionResponse } from '../types'

export const UNKNOWN_NOTE = 'Connection lost; run outcome unknown.'

export interface ReconcileResult {
  status: 'finished' | 'failed' | 'unknown'
  note: string | null
  decision: TicketDecisionResponse | null
  tickets: Ticket[] | null
}

export interface ReconcileDeps {
  tickets: () => Promise<Ticket[]>
  /** Resolves null when no run summary is recorded (HTTP 404). Rejects for any other problem. */
  decision: () => Promise<TicketDecisionResponse | null>
}

/**
 * Decide what really happened to a run whose status could not be read (backend restarted, run record lost,
 * repeated poll failures), using only DURABLE facts: the ticket row and the recorded run summary.
 *  - the matching run completed and the ticket is resolved  -> finished
 *  - the backend recorded that the matching run was blocked -> failed (confirmed)
 *  - anything else                                           -> unknown (never guessed as failed)
 */
export function judgeOutcome(runId: string, ticketId: number, tickets: Ticket[], decision: TicketDecisionResponse | null): Pick<ReconcileResult, 'status' | 'note'> {
  const ticket = tickets.find((t) => t.ticket_id === ticketId) ?? null
  if (decision && decision.run_id === runId && decision.ticket_id === ticketId) {
    if (decision.outcome === 'blocked') return { status: 'failed', note: 'The backend recorded that this run was stopped before it finished.' }
    if (decision.outcome === 'finished' && ticket?.state === 'resolved') return { status: 'finished', note: null }
    if (decision.outcome === 'finished') return { status: 'unknown', note: 'The run finished, but the ticket was not confirmed as resolved (contact was lost during the final step).' }
  }
  return { status: 'unknown', note: UNKNOWN_NOTE }
}

export async function reconcileRun(deps: ReconcileDeps, runId: string, ticketId: number): Promise<ReconcileResult> {
  let tickets: Ticket[]
  try {
    tickets = await deps.tickets()
  } catch {
    return { status: 'unknown', note: UNKNOWN_NOTE, decision: null, tickets: null }
  }
  let decision: TicketDecisionResponse | null
  try {
    decision = await deps.decision()
  } catch {
    return { status: 'unknown', note: UNKNOWN_NOTE, decision: null, tickets }
  }
  return { ...judgeOutcome(runId, ticketId, tickets, decision), decision, tickets }
}
