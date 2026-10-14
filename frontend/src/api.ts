import { API_ORIGIN } from './config'
import { safeText } from './lib/safeText'
import type {
  CashResponse,
  EventsResponse,
  PaymentPreview,
  PaymentReceipt,
  RunStart,
  RunStatus,
  TicketDecisionResponse,
  TicketsResponse,
} from './types'

/**
 * The admin token lives ONLY in this module-level variable (memory). It is never written to storage,
 * cookies, the URL, the console or an error message, and a page reload clears it.
 */
let adminToken = ''

export function setAdminToken(token: string): void {
  adminToken = token.trim()
}
export function clearAdminToken(): void {
  adminToken = ''
}
export function hasAdminToken(): boolean {
  return adminToken.length > 0
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

interface RequestOptions<T> {
  method?: 'GET' | 'POST'
  body?: unknown
  auth?: boolean
  signal?: AbortSignal
  /** Checks that a success response has the shape of THIS backend's reply. */
  guard?: (data: unknown) => data is T
}

export const WRONG_APP_MESSAGE = `The Campus Customs backend is unavailable or the wrong application is running at ${API_ORIGIN}.`

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const hasKeys = (...keys: string[]) => (v: unknown): boolean => isObject(v) && keys.every((k) => k in v)

async function request<T>(path: string, opts: RequestOptions<T> = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (opts.auth) {
    if (!adminToken) throw new ApiError(401, 'locked', 'Unlock the desk with the admin token first.')
    headers.Authorization = `Bearer ${adminToken}`
  }
  let res: Response
  try {
    res = await fetch(`${API_ORIGIN}${path}`, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    // Deliberately generic: never echo the request (it carries the Authorization header).
    throw new ApiError(0, 'unreachable', `Cannot reach the backend at ${API_ORIGIN}. Is it running?`)
  }
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  const ours = isObject(data) && typeof data.code === 'string' // this backend's error shape
  if (!res.ok) {
    if (!ours && (res.status === 404 || res.status === 405 || data === null)) throw new ApiError(res.status, 'wrong_app', WRONG_APP_MESSAGE)
    const obj = (data ?? {}) as { code?: unknown; detail?: unknown }
    const code = typeof obj.code === 'string' ? obj.code : `http_${res.status}`
    const detail = typeof obj.detail === 'string' ? safeText(obj.detail) : `Request failed (HTTP ${res.status}).`
    throw new ApiError(res.status, code, detail)
  }
  if (opts.guard && !opts.guard(data)) throw new ApiError(res.status, 'wrong_app', WRONG_APP_MESSAGE)
  return data as T
}

function shape<T>(...keys: string[]): (data: unknown) => data is T {
  const has = hasKeys(...keys)
  return (data: unknown): data is T => has(data)
}

export const api = {
  tickets: (signal?: AbortSignal) => request<TicketsResponse>('/tickets', { signal, guard: shape<TicketsResponse>('tickets', 'count') }),
  cash: (signal?: AbortSignal) => request<CashResponse>('/cash', { signal, guard: shape<CashResponse>('balance', 'as_of') }),
  events: (params: { runId?: string; after?: string; limit?: number }, signal?: AbortSignal) => {
    const q = new URLSearchParams()
    if (params.runId) q.set('run_id', params.runId)
    if (params.after) q.set('after', params.after)
    q.set('limit', String(params.limit ?? 200))
    return request<EventsResponse>(`/events?${q.toString()}`, { signal, guard: shape<EventsResponse>('events') })
  },
  startRun: (ticketId: number) => request<RunStart>(`/tickets/${ticketId}/run?async=true`, { method: 'POST', auth: true, guard: shape<RunStart>('run_id') }),
  runStatus: (runId: string, signal?: AbortSignal) => request<RunStatus>(`/runs/${encodeURIComponent(runId)}`, { signal, guard: shape<RunStatus>('run_id', 'status') }),
  decision: (ticketId: number, signal?: AbortSignal) => request<TicketDecisionResponse>(`/tickets/${ticketId}/decision`, { signal, guard: shape<TicketDecisionResponse>('run_id', 'decision') }),
  preview: (targetType: 'invoice' | 'rent', targetId: number, ticketId: number, signal?: AbortSignal) =>
    request<PaymentPreview>(`/payments/preview?target_type=${targetType}&target_id=${targetId}&ticket_id=${ticketId}`, { signal, guard: shape<PaymentPreview>('amount_text', 'can_approve') }),
  approve: (body: { target_type: 'invoice' | 'rent'; target_id: number; expected_amount: string; approved_by: string }) =>
    request<PaymentReceipt>('/payments/approve', { method: 'POST', auth: true, body: { ...body, confirm: true }, guard: shape<PaymentReceipt>('payment_id', 'balance_after') }),
}
