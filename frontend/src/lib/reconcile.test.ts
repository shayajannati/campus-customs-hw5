import { describe, expect, it } from 'vitest'
import { reconcileRun, UNKNOWN_NOTE, type ReconcileDeps } from './reconcile'
import type { AgentReport, Ticket, TicketDecision, TicketDecisionResponse } from '../types'

const ticket = (state: 'open' | 'resolved'): Ticket => ({ ticket_id: 102, type: 'rent_notice', subject: 'Rent due', sku: null, size: null, qty: null, lease_id: 1, invoice_id: null, state, raw_status: state, created_at: '', business: null })
const decision: TicketDecision = { ticket_id: 102, status: 'needs_human_approval', summary: 's', facts: [], actions_recommended: [], escalations: [], risks: [], customer_draft: null, specialists_consulted: [] }
const stored = (runId: string, outcome: 'finished' | 'blocked'): TicketDecisionResponse => ({ ticket_id: 102, run_id: runId, outcome, finished_at: '', decision, agent_reports: [] as { agent: string; delegated_by: string; task: string; report: AgentReport }[] })
const deps = (tickets: Ticket[] | Error, dec: TicketDecisionResponse | null | Error): ReconcileDeps => ({
  tickets: async () => { if (tickets instanceof Error) throw tickets; return tickets },
  decision: async () => { if (dec instanceof Error) throw dec; return dec },
})

describe('reconcileRun after the run status could not be read (e.g. backend restarted)', () => {
  it('completed-run recovery: matching finished run + resolved ticket => finished', async () => {
    const r = await reconcileRun(deps([ticket('resolved')], stored('runA', 'finished')), 'runA', 102)
    expect(r.status).toBe('finished')
    expect(r.note).toBeNull()
    expect(r.decision?.run_id).toBe('runA')
  })

  it('unknown outcome: no recorded summary and the ticket is still open => unknown, never failed', async () => {
    const r = await reconcileRun(deps([ticket('open')], null), 'runA', 102)
    expect(r).toMatchObject({ status: 'unknown', note: UNKNOWN_NOTE })
  })

  it('unknown outcome: the recorded summary belongs to a DIFFERENT run => unknown (it proves nothing about this run)', async () => {
    const r = await reconcileRun(deps([ticket('resolved')], stored('olderRun', 'finished')), 'runA', 102)
    expect(r.status).toBe('unknown')
  })

  it('unknown outcome: finished run recorded but the ticket was not confirmed resolved => unknown with an honest note', async () => {
    const r = await reconcileRun(deps([ticket('open')], stored('runA', 'finished')), 'runA', 102)
    expect(r.status).toBe('unknown')
    expect(r.note).toMatch(/not confirmed as resolved/)
  })

  it('unknown outcome: the backend is still unreachable (tickets or decision cannot be read) => unknown', async () => {
    expect((await reconcileRun(deps(new Error('down'), null), 'runA', 102)).status).toBe('unknown')
    expect((await reconcileRun(deps([ticket('open')], new Error('down')), 'runA', 102)).status).toBe('unknown')
  })

  it('confirmed failure: the backend recorded that this run was blocked => failed', async () => {
    const r = await reconcileRun(deps([ticket('open')], stored('runA', 'blocked')), 'runA', 102)
    expect(r.status).toBe('failed')
    expect(r.note).toMatch(/recorded that this run was stopped/)
  })

  it('a resolved ticket is never reported as failed or unknown when its own run completed', async () => {
    for (const outcome of ['finished'] as const) expect((await reconcileRun(deps([ticket('resolved')], stored('runA', outcome)), 'runA', 102)).status).toBe('finished')
  })
})
