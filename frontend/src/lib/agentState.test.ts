import { describe, expect, it } from 'vitest'
import { deriveAgentStates, idleStates } from './agentState'
import { ev } from '../test/fixtures'

describe('deriveAgentStates', () => {
  it('starts everyone idle and invents nothing for agents that never appear', () => {
    const { states } = deriveAgentStates([ev('boss', 'run_started', { prompt: 'p' })], 'running')
    expect(states.boss.phase).toBe('working')
    expect(states.inventory).toEqual(idleStates().inventory)
  })

  it('shows the delegating agent waiting and the delegate working, then done', () => {
    const events = [
      ev('boss', 'run_started'),
      ev('boss', 'delegation', { to: 'facilities', task: 't' }),
      ev('facilities', 'run_started'),
      ev('facilities', 'tool_call', { tool: 'get_rent_obligation', arguments: '{}' }),
    ]
    let r = deriveAgentStates(events, 'running')
    expect(r.states.boss).toEqual({ phase: 'waiting', line: 'Waiting for Facilities' })
    expect(r.states.facilities).toEqual({ phase: 'working', line: 'Calling get_rent_obligation' })
    expect(r.edges).toEqual([{ from: 'boss', to: 'facilities', active: true }])

    r = deriveAgentStates([...events, ev('facilities', 'final_output', { output: '{}' }), ev('boss', 'tool_return', { tool: 'delegate', result: '{}' })], 'running')
    expect(r.states.facilities.phase).toBe('done')
    expect(r.edges[0]?.active).toBe(false)
    expect(r.states.boss).toEqual({ phase: 'working', line: 'Reviewing a specialist report' })
  })

  it('marks unfinished agents as UNKNOWN (not stopped, not done) when contact was lost', () => {
    const events = [ev('boss', 'run_started'), ev('boss', 'delegation', { to: 'accounting', task: 't' }), ev('accounting', 'run_started'), ev('inventory', 'run_started'), ev('inventory', 'final_output', { output: '{}' })]
    const { states, edges } = deriveAgentStates(events, 'unknown')
    expect(states.boss).toEqual({ phase: 'unknown', line: 'Connection lost; status unknown' })
    expect(states.accounting.phase).toBe('unknown')
    expect(states.inventory.phase).toBe('done')
    expect(edges.every((e) => !e.active)).toBe(true)
  })

  it('marks unfinished agents as stopped when the run failed, never as done', () => {
    const events = [ev('boss', 'run_started'), ev('boss', 'delegation', { to: 'accounting', task: 't' }), ev('accounting', 'run_started')]
    const { states, edges } = deriveAgentStates(events, 'failed')
    expect(states.boss.phase).toBe('stopped')
    expect(states.accounting.phase).toBe('stopped')
    expect(edges.every((e) => !e.active)).toBe(true)
  })
})
