import { useState } from 'react'
import { ApiError, api } from '../api'
import { dueText, money } from '../lib/format'
import { usePaymentPreview } from '../hooks/usePaymentPreview'
import type { PaymentReceipt, Ticket } from '../types'
import { Dialog } from './Dialog'

/**
 * Human approval for ONE payment. Nothing here runs by itself: the only code path that sends a payment is the
 * "Approve" button in the confirmation dialog. Every figure shown comes from the server's preview, and the
 * server checks the amount, duplicates and cash again at approval time.
 */
export function ApprovalPanel({
  ticket, requested, unlocked, onNeedUnlock, onPaid,
}: {
  ticket: Ticket
  /** True only when the finished run's own escalations asked for a payment decision. */
  requested: boolean
  unlocked: boolean
  onNeedUnlock: () => void
  onPaid: () => Promise<void>
}) {
  const payable = ticket.business?.payable ?? null
  const paid = ticket.business?.state === 'paid'
  const { preview, error, loading, reload } = usePaymentPreview(ticket, requested && payable !== null)
  const [name, setName] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<PaymentReceipt | null>(null)

  if (ticket.type === 'price_override') {
    return (
      <section className="approval" aria-labelledby="approval-title" data-testid="approval-panel">
        <h2 id="approval-title" className="section-title">Human approval</h2>
        <p>Ticket 103 has <strong>no payment to approve</strong>. A discount, a partial fill and any restock are human decisions made outside this system, and the shop's records contain no purchase price to base a purchase on.</p>
      </section>
    )
  }
  if (!payable) return null
  if (!requested && !paid && !receipt) {
    return (
      <section className="approval" aria-labelledby="approval-title" data-testid="approval-panel">
        <h2 id="approval-title" className="section-title">Human approval</h2>
        <p className="muted">No payment has been requested for this ticket. Run the agent team; if it recommends a payment, the details will appear here for you to review.</p>
      </section>
    )
  }

  const nameOk = /^[A-Za-z][A-Za-z .,'-]{1,78}[A-Za-z.]$/.test(name.trim())
  const canSubmit = unlocked && preview?.can_approve === true && nameOk && reviewed && !busy

  const approve = async () => {
    if (!preview) return
    setBusy(true)
    setProblem(null)
    try {
      const r = await api.approve({ target_type: preview.target_type, target_id: preview.target_id, expected_amount: preview.amount_text, approved_by: name.trim() })
      setReceipt(r)
      setConfirming(false)
      await onPaid()
      await reload()
    } catch (err) {
      setConfirming(false)
      if (err instanceof ApiError && err.status === 401) onNeedUnlock()
      setProblem(err instanceof ApiError ? `${err.message} (nothing was paid)` : 'The payment could not be approved. Nothing was paid.')
      await reload()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="approval" aria-labelledby="approval-title" data-testid="approval-panel">
      <h2 id="approval-title" className="section-title">Human approval</h2>
      {receipt && (
        <div className="receipt tone-good" role="status" data-testid="receipt">
          <strong>✓ Payment recorded</strong>
          <div>{money(receipt.amount)} paid from checking on {receipt.paid_at}, approved by {receipt.approved_by}. Payment #{receipt.payment_id}.</div>
          <div>Checking balance: {money(receipt.balance_before)} → <strong>{money(receipt.balance_after)}</strong></div>
        </div>
      )}
      {paid && !receipt && <p className="notice tone-good" role="status">✓ A payment is already recorded for this obligation.</p>}
      {loading && !preview && <p className="muted">Loading the server's payment details…</p>}
      {error && <p className="notice tone-stopped" role="alert">{error}</p>}
      {preview && (
        <>
          <p className="muted small">The agents recommended a decision. <strong>That recommendation is not an approval.</strong> Nothing is paid until you approve it below. These figures come from the shop's records, read just now.</p>
          <dl className="facts" data-testid="preview">
            <div><dt>Pay</dt><dd>{preview.payee}</dd></div>
            <div><dt>For</dt><dd>{preview.description ?? (preview.target_type === 'rent' ? 'Rent' : 'Invoice')} {preview.billing_period ? `· billing period ${preview.billing_period}` : ''}</dd></div>
            <div><dt>Amount</dt><dd data-testid="preview-amount">{money(preview.amount)}</dd></div>
            <div><dt>Due</dt><dd>{preview.due_date} <span className="muted">({dueText(preview.days_until_due)})</span></dd></div>
            <div><dt>Checking after</dt><dd>{money(preview.cash_before)} → <strong>{money(preview.cash_after)}</strong></dd></div>
          </dl>
          {preview.caveats.map((c) => <p key={c} className="notice tone-attention" role="note">⚠ {c}</p>)}
          {preview.blockers.map((b) => <p key={b.code} className="notice tone-stopped" role="alert">⚠ {b.message}</p>)}
          {preview.can_approve && (
            <form onSubmit={(e) => { e.preventDefault(); if (canSubmit) setConfirming(true) }}>
              <label className="field">
                <span>Your name (recorded as the approver)</span>
                <input type="text" value={name} maxLength={80} autoComplete="off" onChange={(e) => setName(e.target.value)} aria-describedby="name-help" />
                <small id="name-help" className="muted">Letters only. Roles such as "boss" or "agent" are refused.</small>
              </label>
              <label className="check">
                <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
                <span>I have reviewed the amount, payee and cash impact, and I approve this payment.</span>
              </label>
              {!unlocked && <p className="muted small">The desk is locked. <button type="button" className="link" onClick={onNeedUnlock}>Unlock with the admin token</button> to approve.</p>}
              <button type="submit" className="btn danger" disabled={!canSubmit} data-testid="approve-button">Approve payment of {money(preview.amount)}…</button>
            </form>
          )}
          {problem && <p className="notice tone-stopped" role="alert">{problem}</p>}
        </>
      )}
      {confirming && preview && (
        <Dialog title="Confirm payment" onClose={() => setConfirming(false)}>
          <p>You are about to pay <strong>{money(preview.amount)}</strong> to <strong>{preview.payee}</strong> from checking. This changes the cash balance from {money(preview.cash_before)} to {money(preview.cash_after)} and cannot be undone from this page.</p>
          <div className="dialog-actions">
            <button type="button" className="btn ghost" onClick={() => setConfirming(false)} data-autofocus>Cancel</button>
            <button type="button" className="btn danger" onClick={() => void approve()} disabled={busy} data-testid="confirm-approve">{busy ? 'Approving…' : `Yes, pay ${money(preview.amount)}`}</button>
          </div>
        </Dialog>
      )}
    </section>
  )
}
