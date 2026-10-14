import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api, clearAdminToken, hasAdminToken, setAdminToken } from '../api'
import { MAX_POLL_FAILURES, POLL_INTERVAL_MS } from '../config'
import { reconcileRun } from '../lib/reconcile'
import { safeText } from '../lib/safeText'
import type { AuditEvent, RunView, Ticket, TicketDecisionResponse } from '../types'

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = window.setTimeout(resolve, ms)
    signal.addEventListener('abort', () => { window.clearTimeout(t); resolve() }, { once: true })
  })
}

function describe(err: unknown): string {
  return err instanceof ApiError ? safeText(err.message) : 'Something went wrong talking to the backend.'
}

export interface Banner {
  tone: 'error' | 'info'
  text: string
}

export function useDesk() {
  const [tickets, setTickets] = useState<Ticket[]>([])
  const [businessDate, setBusinessDate] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [cash, setCash] = useState<{ balance: number; asOf: string } | null>(null)
  const [cashDelta, setCashDelta] = useState<number | null>(null)
  const [runs, setRuns] = useState<Record<number, RunView>>({})
  const [decisions, setDecisions] = useState<Record<number, TicketDecisionResponse>>({})
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [unlocked, setUnlocked] = useState(hasAdminToken())
  const [unlockRequested, setUnlockRequested] = useState(false)
  const [banner, setBanner] = useState<Banner | null>(null)
  const controllers = useRef(new Map<number, AbortController>())
  const cashRef = useRef<number | null>(null)
  const runsRef = useRef(runs)
  runsRef.current = runs

  const refreshTickets = useCallback(async () => {
    const data = await api.tickets()
    setTickets(data.tickets)
    setBusinessDate(data.business_date)
    setLoaded(true)
    return data.tickets
  }, [])

  const refreshCash = useCallback(async () => {
    const c = await api.cash()
    if (cashRef.current !== null && c.balance !== cashRef.current) setCashDelta(Math.round((c.balance - cashRef.current) * 100) / 100)
    cashRef.current = c.balance
    setCash({ balance: c.balance, asOf: c.as_of })
  }, [])

  const loadDecision = useCallback(async (ticketId: number) => {
    try {
      const d = await api.decision(ticketId)
      setDecisions((prev) => ({ ...prev, [ticketId]: d }))
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) setBanner({ tone: 'error', text: describe(err) })
    }
  }, [])

  /**
   * The run's status could not be read (backend restarted, run record lost, repeated poll failures). Ask the durable
   * records what happened, and only call the run "failed" if the backend confirms it. Otherwise it is "unknown".
   */
  const settleLostRun = useCallback(async (ticketId: number, runId: string, events: AuditEvent[]) => {
    const r = await reconcileRun(
      {
        tickets: async () => (await api.tickets()).tickets,
        decision: async () => {
          try {
            return await api.decision(ticketId)
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) return null
            throw err
          }
        },
      },
      runId,
      ticketId,
    )
    if (r.tickets) setTickets(r.tickets)
    if (r.decision && r.decision.run_id === runId) setDecisions((prev) => ({ ...prev, [ticketId]: r.decision as TicketDecisionResponse }))
    setRuns((p) => ({ ...p, [ticketId]: { runId, ticketId, status: r.status, events: [...events], result: null, note: r.note } }))
  }, [])

  const recheckRun = useCallback(
    async (ticketId: number) => {
      const current = runsRef.current[ticketId]
      if (!current) return
      try {
        const st = await api.runStatus(current.runId)
        if (st.status === 'running') {
          setRuns((p) => ({ ...p, [ticketId]: { ...current, status: 'unknown', note: 'The run is still in progress on the backend. Check again in a moment.' } }))
          return
        }
        setRuns((p) => ({ ...p, [ticketId]: { ...current, status: st.status, result: st, note: st.note } }))
        await Promise.all([refreshTickets(), refreshCash(), loadDecision(ticketId)])
      } catch {
        await settleLostRun(ticketId, current.runId, current.events)
        try {
          await Promise.all([refreshCash(), loadDecision(ticketId)])
        } catch {
          /* still unreachable: the neutral "unknown" notice already says so */
        }
      }
    },
    [loadDecision, refreshCash, refreshTickets, settleLostRun],
  )

  // First load: tickets, cash, and any recorded decision for tickets that already finished.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [list] = await Promise.all([refreshTickets(), refreshCash()])
        if (cancelled) return
        await Promise.all(list.filter((t) => t.state === 'resolved').map((t) => loadDecision(t.ticket_id)))
      } catch (err) {
        if (!cancelled) setBanner({ tone: 'error', text: describe(err) })
      }
    })()
    const live = controllers.current
    return () => {
      cancelled = true
      live.forEach((c) => c.abort())
    }
  }, [refreshTickets, refreshCash, loadDecision])

  const setToken = useCallback((token: string) => {
    setAdminToken(token)
    setUnlocked(hasAdminToken())
    setUnlockRequested(false)
    setBanner(null)
  }, [])
  const lock = useCallback(() => {
    clearAdminToken()
    setUnlocked(false)
  }, [])

  const anyRunning = Object.values(runs).some((r) => r.status === 'running')

  const startRun = useCallback(
    async (ticketId: number) => {
      if (!hasAdminToken()) {
        setUnlockRequested(true)
        return
      }
      setBanner(null)
      setSelectedId(ticketId)
      let cursor: string | undefined
      try {
        const last = await api.events({ limit: 1 })
        cursor = last.events[0]?.audit_id
        const started = await api.startRun(ticketId)
        const ctl = new AbortController()
        controllers.current.set(ticketId, ctl)
        const events: AuditEvent[] = []
        const view = (status: RunView['status'], result: RunView['result']): RunView => ({ runId: started.run_id, ticketId, status, events: [...events], result, note: result?.note ?? null })
        setRuns((p) => ({ ...p, [ticketId]: view('running', null) }))

        let failures = 0
        while (!ctl.signal.aborted) {
          try {
            const batch = await api.events({ runId: started.run_id, after: cursor, limit: 200 }, ctl.signal)
            if (batch.events.length) {
              cursor = batch.events[batch.events.length - 1]?.audit_id ?? cursor
              events.push(...batch.events)
            }
            const st = await api.runStatus(started.run_id, ctl.signal)
            failures = 0
            if (st.status !== 'running') {
              const tail = await api.events({ runId: started.run_id, after: cursor, limit: 200 }, ctl.signal)
              events.push(...tail.events)
              setRuns((p) => ({ ...p, [ticketId]: view(st.status, st) }))
              break
            }
            setRuns((p) => ({ ...p, [ticketId]: view('running', null) }))
          } catch (err) {
            if (ctl.signal.aborted) break
            failures += 1
            const runGone = err instanceof ApiError && err.code === 'run_not_found'
            if (runGone || failures >= MAX_POLL_FAILURES) {
              // Not "failed": the outcome is unknown until the durable records say otherwise.
              await settleLostRun(ticketId, started.run_id, events)
              break
            }
          }
          await sleep(POLL_INTERVAL_MS, ctl.signal)
        }
        controllers.current.delete(ticketId)
        const list = await refreshTickets()
        await refreshCash()
        if (list.find((t) => t.ticket_id === ticketId)) await loadDecision(ticketId)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          clearAdminToken()
          setUnlocked(false)
          setUnlockRequested(true)
          setBanner({ tone: 'error', text: 'The admin token was missing or not accepted. Unlock the desk again.' })
        } else {
          setBanner({ tone: 'error', text: describe(err) })
        }
      }
    },
    [loadDecision, refreshCash, refreshTickets, settleLostRun],
  )

  const afterPayment = useCallback(async () => {
    await Promise.all([refreshTickets(), refreshCash()])
  }, [refreshTickets, refreshCash])

  return {
    tickets, businessDate, loaded, cash, cashDelta, runs, decisions, selectedId, setSelectedId,
    unlocked, unlockRequested, setUnlockRequested, setToken, lock, banner, setBanner,
    anyRunning, startRun, afterPayment, recheckRun,
  }
}
