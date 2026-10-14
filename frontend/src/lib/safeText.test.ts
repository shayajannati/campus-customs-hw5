import { describe, expect, it } from 'vitest'
import { GENERIC_ERROR, safeText } from './safeText'

// Credential-SHAPED but synthetic, assembled at run time (no key-like literal in the source). safeText must mask this shape.
const SYNTHETIC_KEY = ['sk', 'synthetic', 'fixture', 'not', 'a', 'credential'].join('-')
const SYNTHETIC_BEARER = ['Bearer', 'synthetic-test-value-not-real'].join(' ')

describe('safeText: nothing sensitive reaches the screen', () => {
  it('hides absolute paths, including folders with spaces, temp folders, ~ and Windows paths', () => {
    expect(safeText('/Users/example/Desktop/AI Class/HW 5/output/audit_trail.json is not valid JSON')).toBe('[path hidden] is not valid JSON')
    expect(safeText('cannot open /var/folders/rg/zkdx0khj/T/tmpabc/audit_trail.json')).toBe('cannot open [path hidden]')
    expect(safeText('see ~/projects/app/logs/server.log')).toBe('see [path hidden]')
    expect(safeText('C:\\Users\\me\\Documents\\x\\audit.json missing')).toBe('[path hidden] missing')
  })

  it('replaces exception names, stack traces and key-like strings with a fixed sentence', () => {
    expect(safeText('RuntimeError: boom')).toBe(GENERIC_ERROR)
    expect(safeText('Traceback (most recent call last):')).toBe(GENERIC_ERROR)
    expect(safeText('ValueError at parse (file.js:10:5)')).toBe(GENERIC_ERROR)
    expect(safeText(`request failed with ${SYNTHETIC_KEY}`)).toBe(GENERIC_ERROR)
    expect(safeText(`Authorization: ${SYNTHETIC_BEARER}`)).toBe(GENERIC_ERROR)
  })

  it('leaves ordinary messages alone, including slashes, dates and URLs', () => {
    for (const ok of ['The audit trail file is unreadable. It was left unchanged; fix or move it by hand.', 'Pay and/or approve', 'Due 2026-09-02', 'Cannot reach the backend at http://localhost:8000. Is it running?', 'Invoice 501 ($840.00 to Bulldog Print Co) is unpaid, 3 days past due.', 'Checking cash is $3,400.00.'])
      expect(safeText(ok)).toBe(ok)
  })
})
