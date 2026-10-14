import { AGENTS, AGENT_ORDER } from '../lib/agents'
import type { AgentStates, Edge, Phase } from '../lib/agentState'
import type { AgentId, RunView, SpecialistReport, TicketDecision } from '../types'
import { AgentBadge } from './AgentBadge'

const PHASE_LABEL: Record<Phase, { text: string; icon: string }> = {
  idle: { text: 'Idle', icon: '○' },
  working: { text: 'Working', icon: '◔' },
  waiting: { text: 'Waiting', icon: '⋯' },
  done: { text: 'Done', icon: '✓' },
  stopped: { text: 'Stopped', icon: '⚠' },
  unknown: { text: 'Unknown', icon: '?' },
}

const POS: Record<AgentId, { x: number; y: number }> = {
  boss: { x: 180, y: 34 },
  inventory: { x: 44, y: 108 },
  accounting: { x: 104, y: 190 },
  facilities: { x: 256, y: 190 },
  customer_service: { x: 316, y: 108 },
}

function DelegationMap({ states, edges }: { states: AgentStates; edges: Edge[] }) {
  const pairs: [AgentId, AgentId][] = []
  for (let i = 0; i < AGENT_ORDER.length; i++) for (let j = i + 1; j < AGENT_ORDER.length; j++) pairs.push([AGENT_ORDER[i] as AgentId, AGENT_ORDER[j] as AgentId])
  return (
    <figure className="map">
      <svg viewBox="0 0 360 224" role="img" aria-label="Delegation map. Every agent can hand work to every other agent; lines that glow show hand-offs that actually happened in this run.">
        {pairs.map(([a, b]) => (
          <line key={`${a}-${b}`} className="edge" x1={POS[a].x} y1={POS[a].y} x2={POS[b].x} y2={POS[b].y} />
        ))}
        {edges.map((e, i) => (
          <line key={`${e.from}-${e.to}-${i}`} className={`edge used ${e.active ? 'active' : ''}`} x1={POS[e.from].x} y1={POS[e.from].y} x2={POS[e.to].x} y2={POS[e.to].y} markerEnd="url(#arrow)" />
        ))}
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="22" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="#1e6bb8" />
          </marker>
        </defs>
        {AGENT_ORDER.map((id) => (
          <g key={id} transform={`translate(${POS[id].x - 22} ${POS[id].y - 22})`} className={`node phase-${states[id].phase}`}>
            <AgentBadge id={id} size={44} />
          </g>
        ))}
      </svg>
      <figcaption className="muted small">Lines are hand-offs recorded in the audit trail. Nothing is drawn unless it happened.</figcaption>
    </figure>
  )
}

function summaryFor(id: AgentId, decision: TicketDecision | null, reports: SpecialistReport[]): string | null {
  if (id === 'boss') return decision ? decision.summary : null
  const reps = reports.filter((r) => r.agent === id)
  return reps.length ? reps.map((r) => r.report.summary).join(' ') : null
}

export function AgentFloor({
  states, edges, run, decision, reports, hasRun,
}: {
  states: AgentStates
  edges: Edge[]
  run: RunView | null
  decision: TicketDecision | null
  reports: SpecialistReport[]
  hasRun: boolean
}) {
  return (
    <section aria-labelledby="floor-title" className="floor">
      <h2 id="floor-title" className="section-title">Agent floor</h2>
      <DelegationMap states={states} edges={edges} />
      <ul className="agent-list">
        {AGENT_ORDER.map((id) => {
          const meta = AGENTS[id]
          const s = states[id]
          const label = PHASE_LABEL[s.phase]
          const summary = run?.status !== 'running' ? summaryFor(id, decision, reports) : null
          return (
            <li key={id} className={`agent phase-${s.phase}`} data-testid={`agent-${id}`} data-phase={s.phase}>
              <AgentBadge id={id} />
              <div className="agent-body">
                <div className="agent-head">
                  <strong>{meta.name}</strong>
                  <span className={`phase phase-${s.phase}`}><span aria-hidden="true">{label.icon}</span> {label.text}</span>
                </div>
                <div className="muted small">{meta.role}</div>
                <div className="agent-line" data-testid={`agent-line-${id}`}>{hasRun || s.phase !== 'idle' ? s.line : 'Select a ticket and run the team.'}</div>
                {summary && <div className="agent-summary" data-testid={`agent-summary-${id}`}><span className="label">Summary</span> {summary}</div>}
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
