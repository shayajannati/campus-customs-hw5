import { useEffect, useRef, useState } from 'react'
import { money } from '../lib/format'

function usePrefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Counts from the old balance to the new one, so an approved payment is visible as it lands. */
function useCountTo(target: number | null): number | null {
  const [shown, setShown] = useState<number | null>(target)
  const from = useRef<number | null>(target)
  const reduced = usePrefersReducedMotion()
  useEffect(() => {
    if (target === null) return
    if (from.current === null || reduced) {
      from.current = target
      setShown(target)
      return
    }
    const start = from.current
    const t0 = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / 900)
      const eased = 1 - Math.pow(1 - p, 3)
      setShown(Math.round((start + (target - start) * eased) * 100) / 100)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      from.current = target
    }
  }, [target, reduced])
  return shown
}

export function CashPanel({ balance, asOf, delta }: { balance: number | null; asOf: string | null; delta: number | null }) {
  const shown = useCountTo(balance)
  return (
    <section className="cash" aria-labelledby="cash-label">
      <h2 id="cash-label" className="cash-label">Checking balance</h2>
      <p className="cash-amount" aria-live="polite" data-testid="cash-balance" data-value={balance ?? ''}>
        {shown === null ? '—' : money(shown)}
      </p>
      <p className="cash-meta">
        {asOf ? <>as of business date {asOf}</> : 'loading…'}
        {delta !== null && delta !== 0 && (
          <span className="cash-delta" data-testid="cash-delta">
            <span aria-hidden="true">{delta < 0 ? '▼' : '▲'}</span> {delta < 0 ? '−' : '+'}{money(Math.abs(delta))} <span className="sr-only">since the last update</span>
          </span>
        )}
      </p>
    </section>
  )
}
