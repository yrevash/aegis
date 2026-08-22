#!/usr/bin/env node
/**
 * Does every page of every portal actually work?
 *
 * The screenshot sweep (`shoot.mjs`) answers *does it lay out* — no overflow, no blank,
 * no console error. That is not the same question as *does it work*, and the difference
 * matters here: this product renders a stated `Absence` when a figure cannot be sourced,
 * which is correct behaviour and looks identical to a broken screen if you only count
 * pixels. A page showing "the posture endpoint did not answer" is working exactly as
 * designed; a page showing a raw error, or silently nothing, is not.
 *
 * So this walks every section of every portal, signed in as the account that portal is
 * actually for, and classifies each screen:
 *
 *   OK       — rendered, and every request it made succeeded
 *   REFUSED  — rendered, but the backend refused a request (403/401). Names the endpoint,
 *              because a refusal the *nav offered you* is a product defect even when the
 *              guard is right: `portal.ts` calls it "a gap wearing a menu entry".
 *   FAILED   — a 5xx, or a request that never answered
 *   EMPTY    — rendered almost nothing, which no screen should
 *
 *   cd web && node scripts/healthcheck.mjs [--portal ai_team]
 *
 * Ports: dev server :3001, backend :8110 — **not :8000**. Two lanes once checked 8000,
 * concluded the backend was down, and skipped verification while it was up the whole time.
 */

import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'

/** Each portal, its sections (mirroring `lib/portal.ts`), and whose account it is. */
const PORTALS = {
  platform_admin: {
    user: 'admin',
    sections: ['dashboard', 'analytics', 'approvals', 'governance', 'roles', 'forecast',
               'jobs', 'audit', 'database', 'mcp', 'console', 'settings'],
  },
  tenant_admin: {
    user: 'northwind.admin',
    sections: ['dashboard', 'analytics', 'documents', 'approvals', 'governance', 'roles',
               'forecast', 'jobs', 'audit', 'console', 'llmops', 'memory', 'settings'],
  },
  ai_team: {
    user: 'northwind.analyst',
    sections: ['console', 'harness', 'mlops', 'llmops', 'evals', 'tokenopt', 'memory',
               'rag', 'graph', 'cache', 'jobs', 'voice', 'vision', 'guardrails',
               'simulation', 'settings'],
  },
  devops: {
    user: 'devops',
    sections: ['dashboard', 'stack', 'patch', 'security', 'redteam', 'cache', 'latency',
               'audit', 'settings'],
  },
  client: {
    user: 'northwind.client',
    sections: ['console', 'dashboard', 'documents', 'analytics', 'approvals', 'savings',
               'forecast', 'risk', 'memory', 'simulation', 'settings'],
  },
}

const BASE = 'http://localhost:3001'
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i === -1 ? d : process.argv[i + 1] }
const only = arg('portal', null)

const browser = await chromium.launch()
const results = []

for (const [portal, { user, sections }] of Object.entries(PORTALS)) {
  if (only && portal !== only) continue

  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  // Two attempts. A cold dev server can still be compiling `/login` when the first
  // fill lands, and a sign-in that races the form is a harness fault reported as a
  // portal outage — it wrongly failed two whole portals on the previous run.
  let signedIn = false
  let lastErr = null
  for (let attempt = 0; attempt < 2 && !signedIn; attempt += 1) {
    try {
      await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
      await page.waitForSelector('#username', { state: 'visible', timeout: 30_000 })
      await page.fill('#username', user)
      await page.fill('#password', 'demo')
      await Promise.all([
        page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 45_000 }),
        page.click('button[type="submit"]'),
      ])
      signedIn = true
    } catch (err) {
      lastErr = err
      await page.waitForTimeout(2000)
    }
  }
  try {
    if (!signedIn) throw lastErr
  } catch (err) {
    console.log(`\n${portal} (${user}) — SIGN-IN FAILED: ${err.message}`)
    await ctx.close()
    continue
  }

  console.log(`\n══ ${portal}  (as ${user}) ══`)
  for (const section of sections) {
    const bad = []
    const onResp = (r) => {
      if (r.status() >= 400) bad.push({ status: r.status(), url: r.url().replace(BASE, '') })
    }
    page.on('response', onResp)

    await page.setViewportSize({ width: 1440, height: 1000 })
    try {
      await page.goto(`${BASE}/app/${portal}/${section}`, { waitUntil: 'domcontentloaded', timeout: 30_000 })
    } catch { /* a screen holding an SSE stream never settles; measure anyway */ }
    await page.waitForTimeout(5000)

    const m = await page.evaluate(() => {
      const main = document.querySelector('main')
      const text = (main?.innerText ?? '').trim()
      return {
        chars: text.length,
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      }
    })
    page.off('response', onResp)

    // Ignore the dev server's own HMR/chunk noise — it says nothing about the product.
    const real = bad.filter((b) => !/_next|__nextjs|hot-update/.test(b.url))
    const refused = real.filter((b) => b.status === 401 || b.status === 403)
    // A 4xx that is NOT an auth refusal is a request the client should not have sent:
    // wrong shape, missing argument, unusable route. It is a defect, and it must not
    // share a bucket with a refusal — nor fall through to OK, which is what happened
    // when `platform_admin/roles` fired `GET /admin/seats` with no tenant on every
    // load and this check reported the screen healthy because 400 is neither 403 nor 5xx.
    const malformed = real.filter((b) => b.status >= 400 && b.status < 500 &&
                                          b.status !== 401 && b.status !== 403)
    // `/readyz` answering 503 is the probe **working**, not the page breaking. Its
    // contract is to return 503 with a full component body when a required component
    // is down, so a load balancer drains the instance — which is exactly the state the
    // devops overview exists to render, and it renders it (3,582 chars, the failing
    // component named with its remediation). Counting that as a page failure would
    // mean the one screen built to show an outage is red whenever there is one.
    // Every other 5xx, and any other endpoint answering 503, still fails.
    const failed = real.filter(
      (b) => b.status >= 500 && !(b.status === 503 && /\/readyz(\?|$)/.test(b.url)),
    )

    let status = 'OK'
    if (failed.length || malformed.length) status = 'FAILED'
    else if (m.chars < 40) status = 'EMPTY'
    else if (refused.length) status = 'REFUSED'

    const overflow = m.scrollWidth > m.innerWidth
    results.push({ portal, section, status, chars: m.chars, overflow,
                   refused: refused.map((r) => `${r.status} ${r.url}`),
                   failed: [...failed, ...malformed].map((r) => `${r.status} ${r.url}`) })

    const mark = { OK: '✓', REFUSED: '⚠', FAILED: '✗', EMPTY: '✗' }[status]
    const extra = [...refused, ...malformed, ...failed].map((r) => `${r.status} ${r.url}`).join(', ')
    console.log(`  ${mark} ${status.padEnd(8)} ${section.padEnd(12)} ${String(m.chars).padStart(6)} chars` +
                `${overflow ? '  OVERFLOW' : ''}${extra ? `  ← ${extra}` : ''}`)
  }
  await ctx.close()
}

await browser.close()
writeFileSync('healthcheck.json', JSON.stringify(results, null, 2))

const by = results.reduce((a, r) => ({ ...a, [r.status]: (a[r.status] ?? 0) + 1 }), {})
console.log(`\n${results.length} screens: ${JSON.stringify(by)}`)
console.log(`overflow: ${results.filter((r) => r.overflow).length}`)
console.log('→ healthcheck.json')
process.exit(results.some((r) => r.status === 'FAILED' || r.status === 'EMPTY') ? 1 : 0)
