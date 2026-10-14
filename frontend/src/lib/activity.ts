import type { AgentId, AuditEvent } from '../types'
import { isAgentId } from './agents'
import { safeText } from './safeText'

export type ActivityKind =
  | 'started'
  | 'said'
  | 'tool_call'
  | 'tool_result'
  | 'delegated'
  | 'report_received'
  | 'refused'
  | 'finished'
  | 'retry'
  | 'error'
  | 'system'

export interface ActivityItem {
  id: string
  time: string
  agent: AgentId | 'system'
  kind: ActivityKind
  /** One line built from the audit record's own fields. Nothing is invented. */
  text: string
  /** The recorded text behind the line (task, arguments, result, summary), exactly as stored. */
  detail?: string
  /** True when the audit trail itself shortened the stored text (it appends "… [+N chars]"). */
  shortened: boolean
  delegatesTo?: AgentId
}

const SHORTENED = /… \[\+\d+ chars\]$/

function str(v: unknown): string {
  return typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v)
}

function tryJson(text: string): Record<string, unknown> | null {
  try {
    const v: unknown = JSON.parse(text)
    return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/**
 * Turn one audit record into one timeline line, or null if the record adds nothing to show.
 * Every word comes from the record: the event type, the agent, the tool name, the recorded task,
 * arguments, result or summary. A line is never made up to fill a gap.
 */
export function toActivity(e: AuditEvent): ActivityItem | null {
  const agent: AgentId | 'system' = isAgentId(e.agent) ? e.agent : 'system'
  const base = { id: e.audit_id, time: e.timestamp_utc, agent }
  const d = e.details
  const mk = (kind: ActivityKind, text: string, detail?: string, extra: Partial<ActivityItem> = {}): ActivityItem => ({
    ...base,
    kind,
    text,
    detail,
    shortened: detail !== undefined && SHORTENED.test(detail),
    ...extra,
  })

  switch (e.event) {
    case 'run_started':
      return mk('started', 'started work', str(d.prompt))
    case 'model_text':
      return mk('said', 'said', str(d.text))
    case 'tool_call': {
      const tool = str(d.tool)
      if (tool === 'delegate') return null // the 'delegation' record that follows says it better
      if (tool === 'final_result') {
        const args = tryJson(str(d.arguments))
        const summary = args && typeof args.summary === 'string' ? args.summary : undefined
        return mk('tool_call', 'submitted its report', summary ?? str(d.arguments))
      }
      return mk('tool_call', `called ${tool}`, str(d.arguments))
    }
    case 'tool_return': {
      const tool = str(d.tool)
      if (tool === 'delegate') {
        const rep = tryJson(str(d.result))
        const from = rep && typeof rep.agent === 'string' ? rep.agent : ''
        const summary = rep && typeof rep.summary === 'string' ? rep.summary : undefined
        return mk('report_received', from ? `received ${from.replace(/_/g, ' ')}'s report` : 'received a specialist report', summary ?? str(d.result))
      }
      return mk('tool_result', `${tool} returned`, str(d.result))
    }
    case 'delegation': {
      const to = str(d.to)
      return mk('delegated', `delegated to ${to.replace(/_/g, ' ')}`, str(d.task), isAgentId(to) ? { delegatesTo: to } : {})
    }
    case 'delegation_refused':
      return mk('refused', `delegation to ${str(d.to).replace(/_/g, ' ')} refused`, str(d.reason))
    case 'final_output': {
      const out = tryJson(str(d.output))
      const summary = out && typeof out.summary === 'string' ? out.summary : undefined
      return mk('finished', 'finished', summary ?? str(d.output))
    }
    case 'retry':
      return mk('retry', 'was asked to retry', safeText(str(d.message)))
    case 'error':
      return mk('error', str(d.type) === 'UsageLimitExceeded' ? 'stopped at a usage limit' : 'hit an error', safeText(str(d.message)))
    case 'run_summary':
      return mk('system', 'run summary recorded in the audit trail')
    case 'ticket_resolved':
      return mk('system', 'ticket workflow marked resolved (the business issue is not resolved by this)')
    case 'ticket_resolve_failed':
      return mk('error', 'ticket could not be marked resolved', safeText(str(d.message) || str(d.error)))
    default:
      return null // user_prompt duplicates run_started; payment and reset events are not part of a run
  }
}

export function toActivities(events: AuditEvent[]): ActivityItem[] {
  const out: ActivityItem[] = []
  for (const e of events) {
    const item = toActivity(e)
    if (item) out.push(item)
  }
  return out
}
