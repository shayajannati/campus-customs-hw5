import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return name === 'test' ? [] : sources(p)
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [p] : []
  })
}
const files = sources(join(process.cwd(), 'src'))

describe('source hygiene (non-test code)', () => {
  it('finds the source files', () => expect(files.length).toBeGreaterThan(10))
  it('never uses browser storage, cookies, or the console', () => {
    for (const f of files) {
      const text = readFileSync(f, 'utf8')
      expect(text, f).not.toMatch(/localStorage|sessionStorage|document\.cookie|indexedDB/)
      expect(text, f).not.toMatch(/console\.(log|info|warn|error|debug)/)
    }
  })
  it('has no hard-coded credential and no token in a URL', () => {
    for (const f of files) {
      const text = readFileSync(f, 'utf8')
      expect(text, f).not.toMatch(/Bearer\s+[A-Za-z0-9_-]{12,}/)
      expect(text, f).not.toMatch(/[?&]token=/i)
      expect(text, f).not.toMatch(/VITE_[A-Z_]*TOKEN/)
    }
  })
  it('has no reset control in the user-facing dashboard', () => {
    for (const f of files) expect(readFileSync(f, 'utf8'), f).not.toMatch(/database\/reset|Reset desk|Reset Desk/)
  })
  it('only the explicit approve path sends a payment', () => {
    const users = files.filter((f) => readFileSync(f, 'utf8').includes('api.approve('))
    expect(users.map((f) => f.split('/').pop())).toEqual(['ApprovalPanel.tsx'])
  })
})
