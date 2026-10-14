import { describe, expect, it } from 'vitest'
import { toActivities, toActivity } from './activity'
import { ev } from '../test/fixtures'

// Credential-SHAPED but synthetic: assembled at run time so no key-like literal sits in the source. The redaction tests
// need this shape (a provider-style prefix followed by a long token) to prove such text is masked.
const SYNTHETIC_KEY = ['sk', 'synthetic', 'fixture', 'not', 'a', 'credential'].join('-')

describe('toActivity: every line comes from the audit record', () => {
  it('maps a delegation to the recorded task and the recipient', () => {
    const a = toActivity(ev('boss', 'delegation', { to: 'facilities', task: 'Verify the rent notice.' }))
    expect(a).toMatchObject({ kind: 'delegated', agent: 'boss', text: 'delegated to facilities', detail: 'Verify the rent notice.', delegatesTo: 'facilities' })
  })

  it('hides the raw delegate tool call (the delegation record says it) but shows other tool calls with their arguments', () => {
    expect(toActivity(ev('boss', 'tool_call', { tool: 'delegate', arguments: '{"to":"x"}' }))).toBeNull()
    const a = toActivity(ev('facilities', 'tool_call', { tool: 'get_rent_obligation', arguments: '{"lease_id":1}' }))
    expect(a).toMatchObject({ kind: 'tool_call', text: 'called get_rent_obligation', detail: '{"lease_id":1}' })
  })

  it('takes the report summary from final_result arguments when they parse, and the raw text when they do not', () => {
    const ok = toActivity(ev('accounting', 'tool_call', { tool: 'final_result', arguments: '{"summary":"Cash is $3,400.00."}' }))
    expect(ok?.detail).toBe('Cash is $3,400.00.')
    const cut = toActivity(ev('accounting', 'tool_call', { tool: 'final_result', arguments: '{"summary":"Cash is… [+120 chars]' }))
    expect(cut?.detail).toContain('[+120 chars]')
    expect(cut?.shortened).toBe(true)
  })

  it('names the specialist whose report came back, from the recorded report itself', () => {
    const a = toActivity(ev('boss', 'tool_return', { tool: 'delegate', result: '{"agent":"customer_service","summary":"Draft ready."}' }))
    expect(a?.text).toBe("received customer service's report")
    expect(a?.detail).toBe('Draft ready.')
  })

  it('reports a finished agent with its recorded summary, and flags text the audit trail shortened', () => {
    expect(toActivity(ev('boss', 'final_output', { output: '{"summary":"All done."}' }))?.detail).toBe('All done.')
    expect(toActivity(ev('boss', 'final_output', { output: '{"summary":"abc… [+30 chars]' }))?.shortened).toBe(true)
  })

  it('shows errors and refusals as recorded', () => {
    expect(toActivity(ev('inventory', 'error', { type: 'UsageLimitExceeded', message: 'The next request would exceed the request_limit of 16' }))).toMatchObject({ kind: 'error', text: 'stopped at a usage limit', detail: 'The next request would exceed the request_limit of 16' })
    expect(toActivity(ev('inventory', 'delegation_refused', { to: 'boss', reason: 'already in the chain' }))).toMatchObject({ kind: 'refused', detail: 'already in the chain' })
  })

  it('labels system records honestly: resolved means the workflow, not the business issue', () => {
    const a = toActivity(ev('system', 'ticket_resolved', { changed: true }))
    expect(a?.agent).toBe('system')
    expect(a?.text).toMatch(/business issue is not resolved/)
  })

  it('ignores records that would only repeat or are not part of a run, instead of inventing a line', () => {
    expect(toActivity(ev('boss', 'user_prompt', { content: 'x' }))).toBeNull()
    expect(toActivity(ev('human', 'payment_attempt', {}))).toBeNull()
    expect(toActivity(ev('human', 'reset_committed', {}))).toBeNull()
  })

  it('keeps record order and never adds lines', () => {
    const events = [ev('boss', 'run_started', { prompt: 'p' }), ev('boss', 'user_prompt', {}), ev('boss', 'delegation', { to: 'inventory', task: 't' })]
    const items = toActivities(events)
    expect(items.map((i) => i.id)).toEqual([events[0]?.audit_id, events[2]?.audit_id])
  })

  it('never shows raw exception text, paths or key-like strings from an error record (defensive fallback)', () => {
    const a = toActivity(ev('boss', 'error', { type: 'RuntimeError', message: `RuntimeError: failed at /Users/me/Desktop/AI Class/HW 5/data/x.db with ${SYNTHETIC_KEY}` }))
    expect(a?.text).toBe('hit an error')
    expect(a?.detail).toBe('Something went wrong. Details are in the server log.')
    const b = toActivity(ev('boss', 'error', { type: 'InternalError', message: 'The agent step failed. Details are in the server log.' }))
    expect(b?.detail).toBe('The agent step failed. Details are in the server log.')
    const c = toActivity(ev('system', 'ticket_resolve_failed', { message: 'Could not open /var/folders/ab/cd/T/ledger.db' }))
    expect(c?.detail).toBe('Could not open [path hidden]')
  })
})
