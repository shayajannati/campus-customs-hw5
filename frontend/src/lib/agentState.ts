import type { AgentId, AuditEvent } from '../types'
import { AGENT_ORDER, agentName, isAgentId } from './agents'

export type Phase = 'idle' | 'working' | 'waiting' | 'done' | 'stopped' | 'unknown'

export interface AgentLive {
  phase: Phase
  /** Literal description of the agent's most recent recorded event. */
  line: string
}

export type AgentStates = Record<AgentId, AgentLive>

export interface Edge {
  from: AgentId
  to: AgentId
  /** True while the delegated agent has not yet delivered its report. */
  active: boolean
}

export function idleStates(): AgentStates {
  return Object.fromEntries(AGENT_ORDER.map((id) => [id, { phase: 'idle', line: 'Not involved yet' }])) as AgentStates
}

/**
 * Derive what each agent is doing from the run's audit records, in order. An agent only ever gets a
 * state because a record says so. If the run failed, agents that had not finished show "stopped".
 */
export function deriveAgentStates(events: AuditEvent[], runStatus: 'running' | 'finished' | 'failed' | 'unknown' | null): { states: AgentStates; edges: Edge[] } {
  const states = idleStates()
  const edges: Edge[] = []
  const set = (agent: string, phase: Phase, line: string) => {
    if (isAgentId(agent)) states[agent] = { phase, line }
  }
  for (const e of events) {
    const d = e.details
    switch (e.event) {
      case 'run_started':
        set(e.agent, 'working', 'Started work')
        break
      case 'tool_call': {
        const tool = String(d.tool ?? '')
        if (tool === 'delegate') break
        set(e.agent, 'working', tool === 'final_result' ? 'Submitting its report' : `Calling ${tool}`)
        break
      }
      case 'tool_return': {
        const tool = String(d.tool ?? '')
        if (tool === 'delegate') set(e.agent, 'working', 'Reviewing a specialist report')
        else set(e.agent, 'working', `Received ${tool} result`)
        break
      }
      case 'delegation': {
        const to = String(d.to ?? '')
        if (isAgentId(e.agent) && isAgentId(to)) {
          set(e.agent, 'waiting', `Waiting for ${agentName(to)}`)
          edges.push({ from: e.agent, to, active: true })
        }
        break
      }
      case 'final_output':
        set(e.agent, 'done', 'Report delivered')
        if (isAgentId(e.agent)) for (const edge of edges) if (edge.to === e.agent) edge.active = false
        break
      case 'error':
        set(e.agent, 'stopped', `Error: ${String(d.type ?? '')}`)
        break
      default:
        break
    }
  }
  if (runStatus === 'failed') {
    for (const id of AGENT_ORDER) {
      const s = states[id]
      if (s.phase === 'working' || s.phase === 'waiting') states[id] = { phase: 'stopped', line: 'Run stopped before this agent finished' }
    }
    for (const edge of edges) edge.active = false
  }
  if (runStatus === 'unknown') {
    for (const id of AGENT_ORDER) {
      const s = states[id]
      if (s.phase === 'working' || s.phase === 'waiting') states[id] = { phase: 'unknown', line: 'Connection lost; status unknown' }
    }
    for (const edge of edges) edge.active = false
  }
  return { states, edges }
}
