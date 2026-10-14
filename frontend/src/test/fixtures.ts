import type { AuditEvent } from '../types'

let n = 0
export function ev(agent: string, event: string, details: AuditEvent['details'] = {}, extra: Partial<AuditEvent> = {}): AuditEvent {
  n += 1
  return {
    audit_id: `id${n}`,
    timestamp_utc: `2026-10-13T12:00:${String(n % 60).padStart(2, '0')}.000+00:00`,
    run_id: 'run1',
    ticket_id: 102,
    step: n,
    agent,
    parent_agent: null,
    depth: 0,
    event,
    details,
    usage: {},
    model: 'stub-boss',
    run_mode: 'stub',
    ...extra,
  }
}
