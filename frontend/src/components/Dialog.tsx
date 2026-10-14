import { useEffect, useRef, type ReactNode } from 'react'

/** Modal dialog with a focus trap, Escape to close, and focus returned to the opener. */
export function Dialog({ title, onClose, children, labelledBy = 'dialog-title' }: { title: string; onClose: () => void; children: ReactNode; labelledBy?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const node = ref.current
    const focusables = () => Array.from(node?.querySelectorAll<HTMLElement>('button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])') ?? []).filter((el) => !el.hasAttribute('disabled'))
    const target = node?.querySelector<HTMLElement>('[data-autofocus]') ?? focusables()[0]
    target?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'Tab') {
        const items = focusables()
        if (items.length === 0) return
        const first = items[0]
        const last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus()
    }
  }, [onClose])
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={labelledBy} ref={ref}>
        <h2 id={labelledBy}>{title}</h2>
        {children}
      </div>
    </div>
  )
}
