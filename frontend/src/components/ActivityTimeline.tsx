import { useEffect, useRef } from 'react'
import { AGENTS, agentName } from '../lib/agents'
import { clock } from '../lib/format'
import type { ActivityItem } from '../lib/activity'
import { AgentBadge } from './AgentBadge'

/** Live feed built only from audit records. `aria-live` announces new lines to screen readers. */
export function ActivityTimeline({ items, running, ticketId }: { items: ActivityItem[]; running: boolean; ticketId: number | null }) {
  const end = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (running) end.current?.scrollIntoView({ block: 'nearest' })
  }, [items.length, running])
  return (
    <section aria-labelledby="log-title" className="log">
      <h2 id="log-title" className="section-title">Activity timeline{ticketId ? ` · Ticket ${ticketId}` : ''}</h2>
      <p className="muted small">Each line is one record from the audit trail, in the order it was written{running ? '. Updating live.' : '.'}</p>
      <ol className="timeline" role="log" aria-live="polite" aria-relevant="additions" aria-label="Agent activity" data-testid="timeline">
        {items.length === 0 && <li className="empty">No activity yet. Pick a ticket and run the agent team.</li>}
        {items.map((it) => (
          <li key={it.id} className={`event kind-${it.kind}`} data-testid="event" data-kind={it.kind} data-agent={it.agent}>
            {it.agent === 'system' ? <span className="sys-dot" aria-hidden="true">●</span> : <AgentBadge id={it.agent} size={26} />}
            <div className="event-body">
              <div className="event-head">
                <strong style={it.agent === 'system' ? undefined : { color: AGENTS[it.agent].color === '#9cc3ec' ? '#0b2a55' : AGENTS[it.agent].color }}>{it.agent === 'system' ? 'System' : agentName(it.agent)}</strong>
                <span className="event-text">{it.text}</span>
                <time className="muted small" dateTime={it.time}>{clock(it.time)}</time>
              </div>
              {it.detail && (
                <details>
                  <summary>{it.kind === 'delegated' ? 'task' : it.kind === 'tool_call' ? 'arguments' : it.kind === 'tool_result' ? 'result' : 'details'}</summary>
                  <pre>{it.detail}</pre>
                  {it.shortened && <p className="muted small">The audit trail stores a shortened copy of this text.</p>}
                </details>
              )}
            </div>
          </li>
        ))}
        <li ref={end} aria-hidden="true" />
      </ol>
    </section>
  )
}
