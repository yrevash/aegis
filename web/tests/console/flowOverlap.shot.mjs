#!/usr/bin/env node
/**
 * Flow-tab containment sweep — the browser check behind the "the graph overlaps the
 * composer" bug.
 *
 * Not a `node --test` file (the runner globs `tests/**\/*.test.mjs`), because it needs a
 * signed-in browser against a live stack. It lives here rather than in a temp directory
 * so the measurement that justified the fix can be re-run:
 *
 *   cd web && node tests/console/flowOverlap.shot.mjs --tag after [--mode team]
 *
 * What it asserts per width, independently of what any screenshot looks like:
 *
 *   1. **No collision with the composer** — the canvas box's bottom never crosses the
 *      composer's top edge. This is the reported defect: the box used to grow to the
 *      graph's own extent, and React Flow's absolutely-positioned nodes then painted
 *      over the composer sitting under it.
 *   2. **The box clips** — `overflow: hidden` on the canvas, so a node larger than the
 *      viewport pans inside the box instead of escaping it.
 *   3. **The fit is real** — every node fully inside the box, unless the zoom is sitting
 *      on the readability floor, which is the one case where panning is by design.
 *   4. **No horizontal body overflow.**
 *   5. **The graph is on screen** — at least one node rect intersects the canvas.
 *
 * Ports: frontend :3001, backend :8110.
 */

import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`)
  return i === -1 ? fallback : process.argv[i + 1]
}

const BASE = arg('base', 'http://localhost:3001')
const USER = arg('user', 'northwind.admin')
const PASSWORD = arg('password', 'demo')
const PORTAL = arg('portal', 'tenant_admin')
const TAG = arg('tag', 'shot')
const MODE = arg('mode', 'single') // 'single' | 'team'
const QUESTION =
  arg('question', null) ??
  (MODE === 'team'
    ? 'Compare our top two suppliers on risk and cost, and recommend one.'
    : 'What do you know about me?')
const OUT = arg('out', join('shots', 'flow', `${TAG}-${MODE}`))
/** The readability floor in `FlowCanvas.tsx` — below it the canvas pans by design. */
const FLOOR = Number(arg('floor', '0.5'))

/** The window sizes the bug was reported at, plus the short window it breaks fastest in. */
const SIZES = [
  { width: 390, height: 844 },
  { width: 834, height: 1112 },
  { width: 1440, height: 900 },
  { width: 1440, height: 700 },
  { width: 1920, height: 1080 },
]

const problems = []
const note = (p) => {
  problems.push(p)
  console.log(`  x ${p.kind}: ${p.detail}`)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

console.log(`signing in as ${USER} …`)
await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' })
await page.fill('input[name="username"], input#username', USER)
await page.fill('input[name="password"], input#password', PASSWORD)
await Promise.all([
  page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 30_000 }),
  page.click('button[type="submit"]'),
])

await page.goto(`${BASE}/app/${PORTAL}/console`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('#composer-question', { timeout: 30_000 })

if (MODE === 'team') {
  const chip = page.getByRole('button', { name: /^Mode:/ })
  await chip.click()
  await page.getByRole('radio', { name: /Team/i }).first().click()
  await page.keyboard.press('Escape')
}

console.log(`asking (${MODE}): ${QUESTION}`)
await page.fill('#composer-question', QUESTION)
await page.keyboard.press('Enter')

// The run is over when the console's status line says so; it is a pure function of the
// newest run, so it is the one signal that does not flicker per token.
await page.waitForFunction(
  () => /finished|failed|refused|declined|stopped|complete/i.test(
    document.querySelector('p[role="status"]')?.textContent ?? '',
  ),
  undefined,
  { timeout: 180_000 },
).catch(() => console.log('  (status line never settled — measuring anyway)'))
await page.waitForTimeout(1500)

await page.getByRole('tab', { name: /flow/i }).click()
await page.waitForTimeout(1200)

mkdirSync(OUT, { recursive: true })

for (const size of SIZES) {
  const label = `${size.width}x${size.height}`
  await page.setViewportSize(size)
  await page.waitForTimeout(900)

  const m = await page.evaluate(() => {
    const panel = document.getElementById('console-panel-flow')
    const pane = panel?.querySelector('.react-flow')
    const nodes = [...(panel?.querySelectorAll('.react-flow__node') ?? [])]
    const composer = document.getElementById('composer-question')?.closest('form')
    const r = (el) => {
      if (!el) return null
      const b = el.getBoundingClientRect()
      return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, w: b.width, h: b.height }
    }
    const box = pane?.parentElement
    const paneRect = r(pane)
    const escapes = nodes
      .map((n) => ({ id: n.getAttribute('data-id'), rect: r(n) }))
      .filter(
        (n) =>
          paneRect &&
          (n.rect.bottom > paneRect.bottom + 1 ||
            n.rect.top < paneRect.top - 1 ||
            n.rect.right > paneRect.right + 1 ||
            n.rect.left < paneRect.left - 1),
      )
    const onScreen = nodes.filter((n) => {
      const b = n.getBoundingClientRect()
      return paneRect && b.bottom > paneRect.top && b.top < paneRect.bottom
    }).length
    return {
      pane: paneRect,
      clips: box ? getComputedStyle(box).overflow : null,
      composer: r(composer),
      nodeCount: nodes.length,
      onScreen,
      escapes: escapes.slice(0, 6),
      escapeCount: escapes.length,
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      zoom: (() => {
        const vp = panel?.querySelector('.react-flow__viewport')
        const t = vp ? getComputedStyle(vp).transform : ''
        const mm = /matrix\(([^,]+)/.exec(t)
        return mm ? Number(mm[1]).toFixed(3) : null
      })(),
    }
  })

  console.log(
    `${label}: pane ${m.pane ? `${Math.round(m.pane.w)}x${Math.round(m.pane.h)}` : 'none'} ` +
      `zoom ${m.zoom} nodes ${m.nodeCount} onScreen ${m.onScreen} escaped ${m.escapeCount}`,
  )

  if (!m.pane) note({ kind: 'missing', label, detail: `${label}: no .react-flow pane in the flow panel` })
  if (m.clips !== null && !/hidden|clip/.test(m.clips)) {
    note({ kind: 'unclipped', label, detail: `${label}: canvas box overflow is '${m.clips}', so nodes can escape it` })
  }
  // Off-box nodes are only acceptable when the fit has bottomed out on the readability
  // floor — that is the documented pan-inside-the-box case. Above the floor they mean
  // the fit simply did not happen.
  const onFloor = m.zoom !== null && Number(m.zoom) <= FLOOR + 0.01
  if (m.escapeCount > 0 && !onFloor) {
    note({
      kind: 'unfitted',
      label,
      detail: `${label}: ${m.escapeCount} node(s) outside the box at zoom ${m.zoom} (above the ${FLOOR} floor), e.g. ${m.escapes
        .map((e) => `${e.id}@${Math.round(e.rect.top)}..${Math.round(e.rect.bottom)}`)
        .join(', ')} (pane ${Math.round(m.pane?.top ?? 0)}..${Math.round(m.pane?.bottom ?? 0)})`,
    })
  }
  if (m.pane && m.composer && m.pane.bottom > m.composer.top + 1) {
    note({
      kind: 'collision',
      label,
      detail: `${label}: canvas bottom ${Math.round(m.pane.bottom)} crosses composer top ${Math.round(m.composer.top)}`,
    })
  }
  if (m.scrollWidth > m.innerWidth) {
    note({ kind: 'overflow', label, detail: `${label}: scrollWidth ${m.scrollWidth} > innerWidth ${m.innerWidth}` })
  }
  if (m.onScreen === 0) note({ kind: 'blank', label, detail: `${label}: no graph node is visible in the canvas` })

  await page.screenshot({ path: join(OUT, `flow-${label}.png`) })
  writeFileSync(join(OUT, `flow-${label}.json`), JSON.stringify(m, null, 2))
}

await browser.close()
writeFileSync(join(OUT, 'problems.json'), JSON.stringify(problems, null, 2))
console.log(`\n${problems.length} problem(s) → ${join(OUT, 'problems.json')}`)
process.exit(problems.length > 0 ? 1 : 0)
