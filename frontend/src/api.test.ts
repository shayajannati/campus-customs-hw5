import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, WRONG_APP_MESSAGE, api, clearAdminToken, hasAdminToken, setAdminToken } from './api'

// A synthetic stand-in that exists only inside this test file, assembled at run time (not a credential).
const TOKEN = ['unit-test', 'synthetic', 'value', 'not-a-credential', '0123456789'].join('-')

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  const spy = vi.fn(async (url: string, init: RequestInit) => handler(url, init))
  vi.stubGlobal('fetch', spy)
  return spy
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })

beforeEach(() => { clearAdminToken(); localStorage.clear(); sessionStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

describe('admin token handling', () => {
  it('lives in memory only: never in storage or cookies', () => {
    setAdminToken(TOKEN)
    expect(hasAdminToken()).toBe(true)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(document.cookie).toBe('')
  })

  it('is sent only as an Authorization header on protected calls, never in the URL or body', async () => {
    const spy = mockFetch(() => json({ run_id: 'r', ticket_id: 1, status: 'running', run_mode: 'stub' }))
    setAdminToken(TOKEN)
    await api.startRun(1)
    const [url, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(url).not.toContain(TOKEN)
    expect(String(init.body ?? '')).not.toContain(TOKEN)
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`)
  })

  it('does not attach the token to read-only calls', async () => {
    const spy = mockFetch(() => json({ account: 'checking', balance: 1, as_of: 'x' }))
    setAdminToken(TOKEN)
    await api.cash()
    const [, init] = spy.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined()
  })

  it('refuses a protected call while locked, without touching the network', async () => {
    const spy = mockFetch(() => json({}))
    await expect(api.startRun(1)).rejects.toMatchObject({ status: 401, code: 'locked' })
    expect(spy).not.toHaveBeenCalled()
  })

  it('forgets the token on lock', async () => {
    setAdminToken(TOKEN)
    clearAdminToken()
    expect(hasAdminToken()).toBe(false)
    await expect(api.approve({ target_type: 'rent', target_id: 1, expected_amount: '1.00', approved_by: 'Pat Doe' })).rejects.toBeInstanceOf(ApiError)
  })

  it('never puts the token in an error message, whatever fails', async () => {
    setAdminToken(TOKEN)
    mockFetch(() => { throw new TypeError(`network down ${TOKEN}`) })
    const err = (await api.startRun(1).catch((e: unknown) => e)) as Error
    expect(err.message).not.toContain(TOKEN)
    expect(err).toMatchObject({ code: 'unreachable' })
    mockFetch(() => json({ code: 'unauthorized', detail: 'A valid admin token is required.' }, 401))
    const err2 = (await api.startRun(1).catch((e: unknown) => e)) as Error
    expect(err2.message).toBe('A valid admin token is required.')
    expect(err2.message).not.toContain(TOKEN)
  })

  it('never logs to the console', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined))
    setAdminToken(TOKEN)
    mockFetch(() => json({ code: 'busy', detail: 'busy' }, 409))
    await api.startRun(1).catch(() => undefined)
    expect(spies.every((s) => s.mock.calls.length === 0)).toBe(true)
    spies.forEach((s) => s.mockRestore())
  })
})

describe('API origin', () => {
  it('targets http://localhost:8000 by default', async () => {
    const spy = mockFetch(() => json({ account: 'checking', balance: 1, as_of: 'x' }))
    await api.cash()
    expect((spy.mock.calls[0] as unknown as [string])[0]).toBe('http://localhost:8000/cash')
  })
})

describe('unexpected responses and unsafe error text', () => {
  it('says the backend is unavailable or the wrong application when another app answers (404 "Not Found")', async () => {
    mockFetch(() => json({ detail: 'Not Found' }, 404))
    await expect(api.tickets()).rejects.toMatchObject({ code: 'wrong_app', message: WRONG_APP_MESSAGE })
    expect(WRONG_APP_MESSAGE).toMatch(/unavailable or the wrong application is running/)
  })

  it('does the same for an HTML or empty error page, and for a success reply with the wrong shape', async () => {
    mockFetch(() => new Response('<html>nginx</html>', { status: 502 }))
    await expect(api.cash()).rejects.toMatchObject({ code: 'wrong_app' })
    mockFetch(() => json({ hello: 'world' }))
    await expect(api.tickets()).rejects.toMatchObject({ code: 'wrong_app' })
    mockFetch(() => json([1, 2, 3]))
    await expect(api.cash()).rejects.toMatchObject({ code: 'wrong_app' })
  })

  it("does not mislabel this backend's own errors as the wrong application", async () => {
    mockFetch(() => json({ code: 'run_not_found', detail: 'No such run (unknown id, or the server restarted).' }, 404))
    await expect(api.runStatus('x')).rejects.toMatchObject({ code: 'run_not_found', status: 404 })
    mockFetch(() => json({ code: 'busy', detail: 'A payment is in progress.' }, 409))
    await expect(api.tickets()).rejects.toMatchObject({ code: 'busy', message: 'A payment is in progress.' })
  })

  it('masks a path or exception text even if the server sent one (defensive)', async () => {
    mockFetch(() => json({ code: 'audit_unavailable', detail: '/Users/me/Desktop/AI Class/HW 5/output/audit_trail.json is not valid JSON' }, 503))
    const e1 = (await api.events({}).catch((e: unknown) => e)) as Error
    expect(e1.message).toBe('[path hidden] is not valid JSON')
    mockFetch(() => json({ code: 'x', detail: 'RuntimeError: secret thing' }, 500))
    const e2 = (await api.events({}).catch((e: unknown) => e)) as Error
    expect(e2.message).toBe('Something went wrong. Details are in the server log.')
  })
})
