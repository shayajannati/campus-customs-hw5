import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { ActivityTimeline } from './components/ActivityTimeline'
import { AgentFloor } from './components/AgentFloor'
import { ApprovalPanel } from './components/ApprovalPanel'
import { CashPanel } from './components/CashPanel'
import { RunSummary } from './components/RunSummary'
import { TicketBoard } from './components/TicketBoard'
import { TokenDialog } from './components/TokenDialog'
import { useDesk } from './hooks/useDesk'
import { toActivities } from './lib/activity'
import { deriveAgentStates } from './lib/agentState'
import type { AuditEvent } from './types'

export default function App() {
  const desk = useDesk()
  const { tickets, runs, decisions, selectedId } = desk
  const [history, setHistory] = useState<Record<number, AuditEvent[]>>({})

  const ticket = tickets.find((t) => t.ticket_id === selectedId) ?? null
  const run = selectedId !== null ? runs[selectedId] ?? null : null
  const stored = selectedId !== null ? decisions[selectedId] ?? null : null
  const decision = run?.result?.decision ?? stored?.decision ?? null
  const reports = run?.result?.agent_reports ?? stored?.agent_reports ?? []

  // After a page reload there is no live run in memory: show the recorded audit events of the last run, labelled as history.
  const storedRunId = stored?.run_id
  useEffect(() => {
    if (selectedId === null || run || !storedRunId || history[selectedId]) return
    let cancelled = false
    api.events({ runId: storedRunId, limit: 200 }).then((r) => { if (!cancelled) setHistory((h) => ({ ...h, [selectedId]: r.events })) }).catch(() => undefined)
    return () => { cancelled = true }
  }, [selectedId, run, storedRunId, history])

  const events = run?.events ?? (selectedId !== null ? history[selectedId] : undefined) ?? []
  const runStatus = run?.status ?? (events.length ? 'finished' : null)
  const { states, edges } = useMemo(() => deriveAgentStates(events, runStatus), [events, runStatus])
  const items = useMemo(() => toActivities(events), [events])

  const paymentRequested = (decision?.escalations ?? []).some((e) => e.category === 'payment')

  return (
    <div className="desk">
      <header className="masthead">
        <div className="brand">
          <svg width="44" height="44" viewBox="0 0 48 48" aria-hidden="true"><polygon points="24,3 43,13.500 43,34.500 24,45 5,34.500 5,13.500" fill="#dbeafa" /><path d="M16 30V19l8-4.500 8 4.500v11l-8 4.500z" fill="#0b2a55" /></svg>
          <div>
            <h1>Campus Customs <span>Operations Desk</span></h1>
            <p>{desk.businessDate ? `Business date ${desk.businessDate}` : 'Connecting…'} · five agents, one human in charge</p>
          </div>
        </div>
        <CashPanel balance={desk.cash?.balance ?? null} asOf={desk.cash?.asOf ?? null} delta={desk.cashDelta} />
        <div className="lockbox">
          {desk.unlocked ? (
            <>
              <span className="chip tone-good"><span aria-hidden="true" className="chip-icon">✓</span>Desk unlocked (this tab only)</span>
              <button type="button" className="btn ghost" onClick={desk.lock}>Lock</button>
            </>
          ) : (
            <button type="button" className="btn primary" onClick={() => desk.setUnlockRequested(true)} data-testid="unlock-button">Unlock desk</button>
          )}
        </div>
      </header>

      {desk.banner && (
        <p className={`banner tone-${desk.banner.tone === 'error' ? 'stopped' : 'neutral'}`} role="alert" data-testid="banner">
          <span aria-hidden="true">⚠</span> {desk.banner.text}
          <button type="button" className="link" onClick={() => desk.setBanner(null)}>Dismiss</button>
        </p>
      )}

      <main className="grid">
        <div className="col col-board">
          {desk.loaded ? (
            <TicketBoard tickets={tickets} runs={runs} selectedId={selectedId} onSelect={desk.setSelectedId} onRun={(id) => void desk.startRun(id)} anyRunning={desk.anyRunning} />
          ) : (
            <section className="board"><h2 className="section-title">Ticket board</h2><p className="muted" role="status">Loading tickets…</p></section>
          )}
        </div>
        <div className="col col-floor">
          <AgentFloor states={states} edges={edges} run={run} decision={decision} reports={reports} hasRun={runStatus !== null} />
        </div>
        <div className="col col-log">
          <ActivityTimeline items={items} running={runStatus === 'running'} ticketId={selectedId} />
          {!run && events.length > 0 && <p className="muted small">Showing the recorded history of this ticket's last run.</p>}
        </div>
        <div className="col col-summary">
          <RunSummary ticket={ticket} run={run} decision={decision} reports={reports} onRecheck={(id) => void desk.recheckRun(id)} />
        </div>
        <div className="col col-approval">
          {ticket ? (
            <ApprovalPanel key={ticket.ticket_id} ticket={ticket} requested={paymentRequested} unlocked={desk.unlocked} onNeedUnlock={() => desk.setUnlockRequested(true)} onPaid={desk.afterPayment} />
          ) : (
            <section className="approval"><h2 className="section-title">Human approval</h2><p className="muted">Select a ticket. If the agents recommend a payment, you review and approve it here.</p></section>
          )}
        </div>
      </main>
      <footer className="foot">Local development desk. Agents recommend; only a person approves payments. Resolved means the agent workflow finished, not that the business issue is solved.</footer>

      {desk.unlockRequested && <TokenDialog onSubmit={desk.setToken} onClose={() => desk.setUnlockRequested(false)} />}
    </div>
  )
}
