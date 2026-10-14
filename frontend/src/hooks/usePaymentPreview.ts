import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '../api'
import type { PaymentPreview, Ticket } from '../types'

/** Loads the server-verified preview for the ticket's payable. Read-only: it never approves anything. */
export function usePaymentPreview(ticket: Ticket | null, enabled: boolean) {
  const [preview, setPreview] = useState<PaymentPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const payable = ticket?.business?.payable ?? null
  const ticketId = ticket?.ticket_id ?? null
  const type = payable?.target_type
  const id = payable?.target_id
  const stateKey = ticket?.business?.state

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      if (!enabled || !type || id === undefined || ticketId === null) return
      setLoading(true)
      try {
        const p = await api.preview(type, id, ticketId, signal)
        setPreview(p)
        setError(null)
      } catch (err) {
        if (signal?.aborted) return
        setError(err instanceof ApiError ? err.message : 'Could not load the payment details.')
      } finally {
        setLoading(false)
      }
    },
    [enabled, type, id, ticketId],
  )

  useEffect(() => {
    const ctl = new AbortController()
    setPreview(null)
    void reload(ctl.signal)
    return () => ctl.abort()
  }, [reload, stateKey])

  return { preview, error, loading, reload: () => reload() }
}
