import { describe, expect, it } from 'vitest'
import { effectiveState, type RunPhase } from './runState'
import { workflowChip } from './labels'
import type { Ticket } from '../types'

const t = (state: 'open' | 'resolved'): Ticket => ({ ticket_id: 1, type: 'x', subject: 's', sku: null, size: null, qty: null, lease_id: null, invoice_id: null, state, raw_status: state, created_at: '', business: null })
const phases: (RunPhase | null)[] = [null, 'running', 'finished', 'failed', 'unknown']

describe('the status chip and the run summary share one rule, so they never contradict each other', () => {
  it('resolved tickets are never described as stopped or unknown (unless still running)', () => {
    for (const run of phases) {
      const s = effectiveState('resolved', run)
      expect(s === 'resolved' || s === 'running').toBe(true)
    }
  })

  it('"stopped" appears only when the backend confirmed a failure on an open ticket', () => {
    for (const ticket of ['open', 'resolved'] as const)
      for (const run of phases) expect(effectiveState(ticket, run) === 'stopped').toBe(ticket === 'open' && run === 'failed')
  })

  it('lost contact is "unknown", never "stopped"', () => {
    expect(effectiveState('open', 'unknown')).toBe('unknown')
  })

  it('every combination: the chip label matches the effective state', () => {
    const expected = { running: 'Agents working', resolved: 'Workflow resolved', stopped: 'Run stopped, not resolved', unknown: 'Run outcome unknown', open: 'Open' } as const
    for (const ticket of ['open', 'resolved'] as const)
      for (const run of phases) expect(workflowChip(t(ticket), run).label).toBe(expected[effectiveState(ticket, run)])
  })
})
