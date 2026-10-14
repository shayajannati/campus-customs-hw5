import { agentName } from '../lib/agents'
import { businessChip } from '../lib/labels'
import { effectiveState } from '../lib/runState'
import { safeText } from '../lib/safeText'
import type { RunView, SpecialistReport, Ticket, TicketDecision } from '../types'

export function RunSummary({
  ticket, run, decision, reports, onRecheck,
}: {
  ticket: Ticket | null
  run: RunView | null
  decision: TicketDecision | null
  reports: SpecialistReport[]
  onRecheck?: (ticketId: number) => void
}) {
  if (!ticket) return <section className="summary"><h2 className="section-title">Run summary</h2><p className="muted">Select a ticket to see its summary.</p></section>
  const result = run?.result ?? null
  // The chip and this summary use the same rule, so they cannot disagree (see lib/runState.ts).
  const state = effectiveState(ticket.state, run?.status ?? null)
  const stopped = state === 'stopped'
  const note = run?.note ?? result?.note ?? null
  const biz = businessChip(ticket.business?.state)
  const escalations = decision?.escalations ?? []
  return (
    <section aria-labelledby="summary-title" className="summary" data-testid="run-summary">
      <h2 id="summary-title" className="section-title">Ticket {ticket.ticket_id} · what the team did</h2>
      {stopped && (
        <p className="notice tone-stopped" role="status" data-testid="notice-stopped">
          <strong>Run stopped, not resolved.</strong> {safeText(note ?? 'The run did not finish normally.')} The ticket stays open and can be run again.
        </p>
      )}
      {state === 'unknown' && (
        <p className="notice tone-neutral" role="status" data-testid="notice-unknown">
          <strong>Connection lost; run outcome unknown.</strong> {note && !note.startsWith('Connection lost') ? `${safeText(note)} ` : ''}The ticket status shown is what the backend last reported.{' '}
          {onRecheck && <button type="button" className="link" onClick={() => onRecheck(ticket.ticket_id)}>Check again</button>}
        </p>
      )}
      {state === 'resolved' && run?.status === 'finished' && note && <p className="notice tone-neutral" role="status">{safeText(note)}</p>}
      {state === 'running' && <p className="notice tone-neutral" role="status">The agents are working. Summaries appear when they finish.</p>}
      {!decision && state !== 'stopped' && state !== 'unknown' && state !== 'running' && <p className="muted">No run recorded for this ticket yet.</p>}
      {decision && !(stopped && decision.status === 'blocked') && (
        <>
          <p className="decision" data-testid="decision-summary">{decision.summary}</p>
          <p className="muted small">
            Boss's recommendation: <strong>{decision.status === 'needs_human_approval' ? 'needs a human decision' : decision.status}</strong>
            {result?.run_mode === 'stub' && <> · <span className="stub-badge">stub run: scripted agents, no model</span></>}
            {result?.usage && <> · {result.usage.requests} model requests, {result.usage.tool_calls} tool calls</>}
          </p>
          {reports.length > 0 && (
            <ul className="reports">
              {reports.map((r, i) => (
                <li key={`${r.agent}-${i}`} data-testid={`report-${r.agent}`}>
                  <strong>{agentName(r.agent)}</strong> <span className="muted small">(asked by {agentName(r.delegated_by)}: {r.task.replace(/^Ticket \d+\.\s*/, '')})</span>
                  <div>{r.report.summary}</div>
                  {r.report.customer_draft && (
                    <blockquote className="draft"><span className="label">Draft only · a human reviews before anything is sent</span>{r.report.customer_draft}</blockquote>
                  )}
                </li>
              ))}
            </ul>
          )}
          <h3>Waiting on a human</h3>
          {escalations.length === 0 && <p className="muted">Nothing was escalated.</p>}
          <ul className="escalations">
            {escalations.map((e, i) => (
              <li key={i}><strong>{e.decision_needed}</strong> <span className="muted small">({e.category.replace(/_/g, ' ')})</span><div>{e.recommendation}</div></li>
            ))}
          </ul>
        </>
      )}
      <h3>Outstanding in the business <span className="muted small">(from current records, not from the agents)</span></h3>
      <div className="chips">{biz && <span className={`chip tone-${biz.tone}`}><span aria-hidden="true" className="chip-icon">{biz.icon}</span>{biz.label}</span>}</div>
      <ul className="pending" data-testid="outstanding">
        {(ticket.business?.pending ?? []).map((p) => <li key={p}>{p}</li>)}
        {(ticket.business?.pending ?? []).length === 0 && <li>Nothing outstanding.</li>}
      </ul>
      {ticket.type === 'price_override' && (
        <p className="muted small">Ticket 103 needs a human decision about a discount and about the 12-unit shortfall. No purchase price exists in the shop's records, so none is proposed and no payment is prepared.</p>
      )}
    </section>
  )
}
