import type { TicketState } from '../types'

/** The run phase the page knows about. 'unknown' means contact was lost and the outcome could not be confirmed. */
export type RunPhase = 'running' | 'finished' | 'failed' | 'unknown'
export type EffectiveState = 'running' | 'resolved' | 'stopped' | 'unknown' | 'open'

/**
 * ONE place decides what to say about a ticket's workflow, so the status chip and the run summary can never
 * disagree. The ticket's stored state wins once it is 'resolved' (it is what the database says). "Stopped" is
 * used only for a failure the backend confirmed; lost contact is "unknown", never "stopped".
 */
export function effectiveState(ticketState: TicketState, run: RunPhase | null): EffectiveState {
  if (run === 'running') return 'running'
  if (ticketState === 'resolved') return 'resolved'
  if (run === 'failed') return 'stopped'
  if (run === 'unknown') return 'unknown'
  return 'open'
}
