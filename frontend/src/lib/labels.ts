import type { BusinessState, Ticket } from '../types'
import { effectiveState, type RunPhase } from './runState'

export type Tone = 'neutral' | 'good' | 'attention' | 'stopped'

export interface Chip {
  label: string
  tone: Tone
  /** Text glyph that repeats the meaning, so status never relies on colour alone. */
  icon: string
}

export function workflowChip(ticket: Ticket, runStatus: RunPhase | null): Chip {
  switch (effectiveState(ticket.state, runStatus)) {
    case 'running':
      return { label: 'Agents working', tone: 'neutral', icon: '◔' }
    case 'resolved':
      return { label: 'Workflow resolved', tone: 'good', icon: '✓' }
    case 'stopped':
      return { label: 'Run stopped, not resolved', tone: 'stopped', icon: '⚠' }
    case 'unknown':
      return { label: 'Run outcome unknown', tone: 'neutral', icon: '?' }
    default:
      return { label: 'Open', tone: 'neutral', icon: '○' }
  }
}

export function businessChip(state: BusinessState | undefined): Chip | null {
  switch (state) {
    case 'awaiting_human_decision':
      return { label: 'Needs a human decision', tone: 'attention', icon: '!' }
    case 'paid':
      return { label: 'Payment recorded', tone: 'good', icon: '✓' }
    case 'no_payment_applicable':
      return { label: 'No payment involved', tone: 'neutral', icon: '–' }
    default:
      return null
  }
}

export function ticketTitle(ticket: Ticket): string {
  return `Ticket ${ticket.ticket_id}`
}
