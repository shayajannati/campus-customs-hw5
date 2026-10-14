import { describe, expect, it } from 'vitest'
import { businessChip, workflowChip } from './labels'
import { dueText, money } from './format'
import type { Ticket } from '../types'

const t = (state: 'open' | 'resolved'): Ticket => ({ ticket_id: 1, type: 'x', subject: 's', sku: null, size: null, qty: null, lease_id: null, invoice_id: null, state, raw_status: state, created_at: '', business: null })

describe('status chips never rely on colour alone', () => {
  it('distinguishes workflow states with text and an icon', () => {
    expect(workflowChip(t('open'), null)).toMatchObject({ label: 'Open', icon: '○' })
    expect(workflowChip(t('resolved'), 'finished')).toMatchObject({ label: 'Workflow resolved', icon: '✓' })
    expect(workflowChip(t('open'), 'running').label).toBe('Agents working')
  })

  it('lost contact is a neutral "unknown", not a failure', () => {
    const c = workflowChip(t('open'), 'unknown')
    expect(c).toMatchObject({ label: 'Run outcome unknown', tone: 'neutral', icon: '?' })
    expect(c.label).not.toMatch(/stopped|failed/i)
  })

  it('a ticket the database says is resolved is never shown as stopped or unknown', () => {
    for (const run of ['failed', 'unknown', 'finished'] as const) expect(workflowChip(t('resolved'), run).label).toBe('Workflow resolved')
  })

  it('a failed run is never shown as resolved', () => {
    const c = workflowChip(t('open'), 'failed')
    expect(c.label).toMatch(/not resolved/)
    expect(c.tone).toBe('stopped')
  })

  it('keeps business state separate from workflow state', () => {
    expect(businessChip('awaiting_human_decision')).toMatchObject({ label: 'Needs a human decision', tone: 'attention' })
    expect(businessChip('paid')?.label).toBe('Payment recorded')
    expect(businessChip(undefined)).toBeNull()
  })
})

describe('formatting', () => {
  it('formats money and due text', () => {
    expect(money(840)).toBe('$840.00')
    expect(dueText(-3)).toBe('3 days overdue')
    expect(dueText(2)).toBe('due in 2 days')
    expect(dueText(null)).toBe('')
  })
})
