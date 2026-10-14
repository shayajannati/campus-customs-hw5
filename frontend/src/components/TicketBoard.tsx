import { businessChip, workflowChip, type Chip } from '../lib/labels'
import type { RunView, Ticket } from '../types'

function ChipView({ chip }: { chip: Chip }) {
  return (
    <span className={`chip tone-${chip.tone}`}>
      <span aria-hidden="true" className="chip-icon">{chip.icon}</span>
      {chip.label}
    </span>
  )
}

const KIND: Record<string, string> = {
  customer_order: 'Customer order',
  rent_notice: 'Rent notice',
  price_override: 'Bulk discount request',
}

export function TicketBoard({
  tickets, runs, selectedId, onSelect, onRun, anyRunning,
}: {
  tickets: Ticket[]
  runs: Record<number, RunView>
  selectedId: number | null
  onSelect: (id: number) => void
  onRun: (id: number) => void
  anyRunning: boolean
}) {
  return (
    <section aria-labelledby="board-title" className="board">
      <h2 id="board-title" className="section-title">Ticket board</h2>
      <ul className="ticket-list">
        {tickets.map((t) => {
          const run = runs[t.ticket_id]
          const runStatus = run?.status ?? null
          const wf = workflowChip(t, runStatus)
          const biz = businessChip(t.business?.state)
          const resolved = t.state === 'resolved'
          return (
            <li key={t.ticket_id} className={`ticket ${selectedId === t.ticket_id ? 'selected' : ''} ${resolved ? 'is-resolved' : ''}`} data-testid={`ticket-${t.ticket_id}`} data-state={t.state}>
              <button type="button" className="ticket-hit" aria-pressed={selectedId === t.ticket_id} onClick={() => onSelect(t.ticket_id)}>
                <span className="ticket-kicker">{KIND[t.type] ?? t.type}</span>
                <span className="ticket-title">Ticket {t.ticket_id}: {t.subject}</span>
              </button>
              {resolved && <span className="stamp" aria-hidden="true">RESOLVED</span>}
              <div className="chips">
                <ChipView chip={wf} />
                {biz && <ChipView chip={biz} />}
              </div>
              {t.business && t.business.pending.length > 0 && (
                <ul className="pending" aria-label={`Still outstanding for ticket ${t.ticket_id}`}>
                  {t.business.pending.map((p) => <li key={p}>{p}</li>)}
                </ul>
              )}
              <div className="ticket-actions">
                <button type="button" className="btn primary" onClick={() => onRun(t.ticket_id)} disabled={resolved || anyRunning} title={resolved ? 'This ticket already has a finished run' : anyRunning ? 'A run is in progress' : undefined}>
                  {runStatus === 'running' ? 'Running…' : resolved ? 'Run finished' : 'Run agent team'}
                </button>
              </div>
            </li>
          )
        })}
      </ul>
      <p className="board-note">
        <strong>Resolved</strong> means the agent workflow finished. It does not mean the business problem is solved or that anything was paid.
      </p>
    </section>
  )
}
