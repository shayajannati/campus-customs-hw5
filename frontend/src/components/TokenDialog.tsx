import { useState, type FormEvent } from 'react'
import { Dialog } from './Dialog'

/** The admin token is typed here and kept in memory only (see src/api.ts). It is never stored or sent in a URL. */
export function TokenDialog({ onSubmit, onClose }: { onSubmit: (token: string) => void; onClose: () => void }) {
  const [value, setValue] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (value.trim()) onSubmit(value)
  }
  return (
    <Dialog title="Unlock the desk" onClose={onClose}>
      <form onSubmit={submit}>
        <p>
          Running the agents and approving payments need the admin token you set as <code>CAMPUS_ADMIN_TOKEN</code> when you started the backend. It stays in this tab's memory only: it is not saved, not put in a URL, and it is forgotten when you reload or lock the desk.
        </p>
        <label className="field">
          <span>Admin token</span>
          <input data-autofocus type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        <div className="dialog-actions">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn primary" disabled={!value.trim()}>Unlock</button>
        </div>
      </form>
    </Dialog>
  )
}
