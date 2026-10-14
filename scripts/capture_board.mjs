// Captures a REAL screenshot of the React dashboard (talking to the real backend) for one ticket.
// Usage: node scripts/capture_board.mjs <ticketId> <outPng> [appUrl]
// Uses the installed Google Chrome through playwright-core (frontend/node_modules). Makes no model calls.
import { chromium } from '../frontend/node_modules/playwright-core/index.mjs'
import { writeFileSync } from 'node:fs'

const [ticketId, out, url = 'http://localhost:5173'] = process.argv.slice(2)
if (!ticketId || !out) throw new Error('usage: node scripts/capture_board.mjs <ticketId> <outPng> [appUrl]')

const browser = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
try {
  await page.goto(url)
  await page.waitForSelector(`[data-testid="ticket-${ticketId}"]`, { timeout: 15000 })
  await page.click(`[data-testid="ticket-${ticketId}"] .ticket-hit`)
  await page.waitForSelector('[data-testid="decision-summary"]', { timeout: 15000 })
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="event"]').length > 5, null, { timeout: 15000 })
  // If the ticket has a payment request, wait until the server's payment details have loaded (or the panel says there is nothing to approve).
  await page.waitForFunction(() => !/Loading the server/.test(document.querySelector('[data-testid="approval-panel"]')?.textContent ?? ''), null, { timeout: 20000 })
  await page.waitForTimeout(1200) // let the cash count-up and stamp animation settle
  const facts = await page.evaluate((id) => {
    const t = document.querySelector(`[data-testid="ticket-${id}"]`)
    return {
      ticketState: t?.dataset.state ?? null,
      stampShown: !!t?.querySelector('.stamp'),
      chips: [...(t?.querySelectorAll('.chip') ?? [])].map((c) => c.textContent),
      events: document.querySelectorAll('[data-testid="event"]').length,
      cash: document.querySelector('[data-testid="cash-balance"]')?.getAttribute('data-value'),
      agentPhases: Object.fromEntries([...document.querySelectorAll('[data-testid^="agent-"][data-phase]')].map((a) => [a.dataset.testid, a.dataset.phase])),
      title: document.title,
    }
  }, ticketId)
  await page.screenshot({ path: out, fullPage: true })
  writeFileSync(out.replace(/\.png$/, '.json'), JSON.stringify({ ticketId: Number(ticketId), capturedAt: new Date().toISOString(), url, viewport: '1440x1000 full page', dom: facts }, null, 2))
  console.log(JSON.stringify(facts))
} finally {
  await browser.close()
}
