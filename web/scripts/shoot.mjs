#!/usr/bin/env node
/**
 * Screenshot + responsive-fit sweep for one portal.
 *
 * **Why this is a committed script and not a scratch file.** The previous version of this
 * harness lived in a session temp directory and went with it, so the next pass had no way
 * to reproduce the measurement that had justified the previous one. A verification you
 * cannot re-run is an assertion, not evidence.
 *
 * What it asserts, per screen per width, independently of anything an implementer says:
 *
 *   1. **No horizontal body overflow** — `documentElement.scrollWidth === innerWidth`.
 *      This is the defect class that has landed four times in this codebase: a flex/grid
 *      child defaults to `min-width:auto`, so one long id sets the track width and the
 *      *page* scrolls sideways instead of the table. Invisible at 1440, and the whole
 *      experience at 390.
 *   2. **No console errors** while the screen loads.
 *   3. **The screen actually rendered** — not a blank shell behind a dead backend, which
 *      is the failure mode that silently makes every other check pass.
 *
 *   cd web && node scripts/shoot.mjs --portal ai_team [--user northwind.analyst]
 *
 * Writes a PNG per screen per width plus `problems.json`. Exits non-zero if anything failed,
 * so it is usable as a gate rather than as a thing somebody reads and forgets.
 *
 * **It lives under `web/` because `playwright` does.** ESM resolves a bare import from the
 * importing *file's* location, not the working directory, so the same script one level up
 * cannot see `web/node_modules` no matter where you run it from.
 *
 * **Default ports, because getting these wrong silently invalidates the whole run:** the
 * dev server is `:3001` and the backend is **`:8110`**, not `:8000`. Two implementation
 * lanes checked `:8000`, concluded no backend was running, and skipped their responsive
 * verification entirely — while both services were up the whole time.
 */

import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const WIDTHS = [390, 834, 1440, 1920]

/** Portal → its sections, mirroring `web/src/lib/portal.ts` ROLE_SECTIONS. */
const SECTIONS = {
  ai_team: ['console', 'harness', 'mlops', 'llmops', 'evals', 'tokenopt', 'memory', 'rag',
            'graph', 'cache', 'jobs', 'voice', 'vision', 'guardrails', 'simulation', 'settings'],
  platform_admin: ['dashboard', 'analytics', 'approvals', 'governance', 'roles', 'forecast',
                   'jobs', 'audit', 'database', 'mcp', 'console', 'settings'],
  tenant_admin: ['dashboard', 'analytics', 'documents', 'approvals', 'governance', 'roles',
                 'forecast', 'jobs', 'audit', 'console', 'llmops', 'memory', 'settings'],
  devops: ['dashboard', 'stack', 'patch', 'security', 'redteam', 'cache', 'latency', 'audit', 'settings'],
  client: ['console', 'dashboard', 'documents', 'analytics', 'approvals', 'savings',
           'forecast', 'risk', 'memory', 'simulation', 'settings'],
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`)
  return i === -1 ? fallback : process.argv[i + 1]
}

const PORTAL = arg('portal', 'ai_team')
const USER = arg('user', 'northwind.analyst')
const PASSWORD = arg('password', 'demo')
const BASE = arg('base', 'http://localhost:3001')
const OUT = arg('out', join('web', 'shots', PORTAL))

if (!SECTIONS[PORTAL]) {
  console.error(`unknown portal '${PORTAL}'. one of: ${Object.keys(SECTIONS).join(', ')}`)
  process.exit(1)
}

const problems = []
const note = (p) => {
  problems.push(p)
  console.log(`  ✗ ${p.kind}: ${p.detail}`)
}

/**
 * Sign in through the real form and hand back the token the app stored.
 *
 * Through the UI rather than by POSTing `/v1/auth/login` and injecting the result: the
 * point is to exercise what a person meets, and a token planted directly would skip
 * whatever the app does on a real sign-in.
 */
async function signIn(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
  await page.fill('input[name="username"], input#username', USER)
  await page.fill('input[name="password"], input#password', PASSWORD)
  await Promise.all([
    page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 }),
    page.click('button[type="submit"]'),
  ])
}

const browser = await chromium.launch()
const context = await browser.newContext()
const page = await context.newPage()

console.log(`signing in as ${USER} …`)
try {
  await signIn(page)
  console.log(`signed in → ${page.url()}\n`)
} catch (err) {
  console.error(`sign-in failed: ${err.message}`)
  console.error('Is the frontend on :3001 and the backend on :8110?')
  await browser.close()
  process.exit(1)
}

mkdirSync(OUT, { recursive: true })

for (const section of SECTIONS[PORTAL]) {
  console.log(`${section}`)
  for (const width of WIDTHS) {
    const errors = []
    const onErr = (m) => m.type() === 'error' && errors.push(m.text())
    page.on('console', onErr)

    // Track the responses behind "Failed to load resource", which carries no URL of its
    // own. `/readyz` answering 503 is its documented contract — the body names the failing
    // component and a load balancer drains the instance — so the devops overview reading
    // it is the screen working, not breaking. Without this the one screen built to show
    // an outage reports four console errors whenever there is one.
    const responseFailures = []
    const onResp = (r) => { if (r.status() >= 400) responseFailures.push({ status: r.status(), url: r.url() }) }
    page.on('response', onResp)

    await page.setViewportSize({ width, height: 1000 })
    try {
      await page.goto(`${BASE}/app/${PORTAL}/${section}`, {
        waitUntil: 'networkidle',
        timeout: 30_000,
      })
    } catch {
      // networkidle never settles on a screen holding an open SSE stream — that is
      // normal here, not a failure. Fall back to a settle pause and measure anyway.
      await page.waitForTimeout(2500)
    }
    await page.waitForTimeout(900)

    const m = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      // Blank-shell guard: a dead backend renders chrome and nothing else, which would
      // otherwise pass every check on this page.
      mainText: (document.querySelector('main')?.innerText ?? '').trim().length,
    }))

    if (m.scrollWidth > m.innerWidth) {
      note({ kind: 'overflow', section, width,
             detail: `${section} @${width}: scrollWidth ${m.scrollWidth} > innerWidth ${m.innerWidth} (+${m.scrollWidth - m.innerWidth}px)` })
    }
    // Re-measure before calling a screen blank. A heavy screen can still be compiling
    // on a cold dev server, and reporting that as "did it render?" sends someone hunting
    // a defect that is really a stopwatch — `jobs` did exactly this at 834 and 1920 while
    // rendering 14,895 characters at 390 and 1440 in the same run.
    if (m.mainText < 40) {
      await page.waitForTimeout(6000)
      const again = await page.evaluate(
        () => (document.querySelector('main')?.innerText ?? '').trim().length,
      )
      if (again < 40) {
        note({ kind: 'blank', section, width,
               detail: `${section} @${width}: <main> holds ${again} chars after a retry — did it render?` })
      }
    }
    const expected = (f) => f.status === 503 && /\/readyz(\?|$)/.test(f.url)
    const allExpected = responseFailures.length > 0 && responseFailures.every(expected)
    for (const e of errors.slice(0, 3)) {
      // A bare resource-load error whose every underlying failure was expected is noise.
      if (/Failed to load resource/.test(e) && allExpected) continue
      note({ kind: 'console', section, width, detail: `${section} @${width}: ${e.slice(0, 200)}` })
    }

    await page.screenshot({
      path: join(OUT, `${section}-${width}.png`),
      fullPage: width === 1440,
    })
    page.off('console', onErr)
    page.off('response', onResp)
  }
}

await browser.close()

writeFileSync(join(OUT, 'problems.json'), JSON.stringify(problems, null, 2))
const counts = problems.reduce((a, p) => ({ ...a, [p.kind]: (a[p.kind] ?? 0) + 1 }), {})
console.log(`\n${problems.length} problem(s) ${JSON.stringify(counts)} → ${join(OUT, 'problems.json')}`)
process.exit(problems.length > 0 ? 1 : 0)
