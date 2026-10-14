/** The API origin. The dashboard talks to http://localhost:8000 unless an isolated test overrides it. */
export const API_ORIGIN: string = (import.meta.env.VITE_API_ORIGIN ?? 'http://localhost:8000').replace(/\/+$/, '')

export const POLL_INTERVAL_MS = 700
export const MAX_POLL_FAILURES = 5
