#!/usr/bin/env node
/**
 * The top-bar `Aa` control, driven by a real mouse, a real finger and the keyboard.
 *
 * Not a `node --test` file (the runner globs `tests/**\/*.test.mjs`), because it needs a
 * signed-in browser against a live stack. It lives here rather than in a temp directory
 * because the measurement that justified the fix has to be re-runnable:
 *
 *   cd web && node tests/settings/textSizeMouse.shot.mjs
 *
 * The defect it exists for: the popover's rows are `sr-only` radios inside `<label>`s, a
 * label takes no focus, so a mouse press moved focus to nothing, the wrapper's `onBlur`
 * read `relatedTarget === null` as "focus left me", and the panel unmounted **on
 * mousedown** — before the click could reach the radio. The accessibility control the
 * product owner asked for was therefore keyboard-only, which is precisely backwards.
 * `tests/settings/menuDismiss.test.mjs` holds the rule; this holds the gesture.
 *
 * What it asserts:
 *
 *   1. **The panel survives `mouse.down()`** — the regression itself. A press with no
 *      release must leave `aria-expanded="true"` with the radios still in the DOM.
 *   2. **A plain click applies the step** — root font-size 125%, `aegis.textScale`
 *      persisted, and the trigger's `aria-label` re-announced.
 *   3. **A touch tap applies it too**, on a 390px phone context.
 *   4. **The keyboard still works** — arrow keys move the roving tab stop and choose.
 *   5. **It still dismisses** — `Escape` closes it and returns the caret to the trigger,
 *      and a press outside closes it.
 *
 * Ports: frontend :3001, backend :8110.
 */

import { chromium } from 'playwright'

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`)
  return i === -1 ? fallback : process.argv[i + 1]
}

const BASE = arg('base', 'http://localhost:3001')
const USER = arg('user', 'admin')
const PASSWORD = arg('password', 'demo')
const SCREEN = arg('screen', '/app/platform_admin/dashboard')

const TRIGGER = 'button[aria-label^="Text size"]'
const LARGEST = 'label:has(input[type=radio][value="125"])'

const problems = []
const fail = (detail) => {
  problems.push(detail)
  console.log(`  x ${detail}`)
}
const pass = (detail) => console.log(`  ok ${detail}`)

/** Everything the control claims about itself, read off the live DOM. */
const state = (page) =>
  page.evaluate(() => {
    const trigger = document.querySelector('button[aria-label^="Text size"]')
    return {
      root: getComputedStyle(document.documentElement).fontSize,
      stored: localStorage.getItem('aegis.textScale'),
      label: trigger?.getAttribute('aria-label') ?? null,
      expanded: trigger?.getAttribute('aria-expanded') ?? null,
      radios: document.querySelectorAll('input[type=radio][value="125"]').length,
    }
  })

/** A clean slate, then the menu open. `tap` opens it with a finger instead of a mouse. */
async function open(page, { tap = false } = {}) {
  await page.goto(`${BASE}${SCREEN}`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector(TRIGGER, { timeout: 30_000 })
  await page.evaluate(() => localStorage.removeItem('aegis.textScale'))
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.waitForSelector(TRIGGER, { timeout: 30_000 })
  if (tap) await page.tap(TRIGGER)
  else await page.click(TRIGGER)
  await page.waitForTimeout(200)
}

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

// ── 1 · the press itself, with no release ─────────────────────────────────────
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()
  await signIn(page)
  await open(page)

  const box = await page.locator(LARGEST).first().boundingBox()
  if (box === null) {
    fail('the 125% row is not in the DOM after opening the menu')
  } else {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    const held = await state(page)
    if (held.expanded !== 'true' || held.radios === 0) {
      fail(`mouse.down() closed the menu: aria-expanded=${held.expanded}, ${held.radios} radios`)
    } else {
      pass('the panel survives mousedown')
    }

    await page.mouse.up()
    await page.waitForTimeout(300)
    const after = await state(page)
    if (after.root !== '20px' || after.stored !== '125') {
      fail(`the release did not apply the step: root=${after.root}, stored=${after.stored}`)
    } else {
      pass(`mouse press + release applies 125% (root ${after.root}, stored ${after.stored})`)
    }
  }

  // ── 2 · a plain click, from a clean slate ──
  await open(page)
  await page.click(LARGEST)
  await page.waitForTimeout(300)
  const clicked = await state(page)
  if (clicked.root !== '20px' || clicked.stored !== '125' || !/125 percent/.test(clicked.label)) {
    fail(`click did not apply the step: ${JSON.stringify(clicked)}`)
  } else {
    pass('a plain click applies it and the trigger re-announces itself')
  }

  // ── 4 · the keyboard, which must not have been traded away ──
  await open(page)
  await page.locator('input[type=radio][value="90"]').first().focus()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.waitForTimeout(300)
  const typed = await state(page)
  if (typed.root !== '20px' || typed.stored !== '125') {
    fail(`the keyboard no longer chooses a step: ${JSON.stringify(typed)}`)
  } else {
    pass('arrow keys still choose a step')
  }

  // ── 5 · it still dismisses ──
  await page.keyboard.press('Escape')
  await page.waitForTimeout(200)
  const escaped = await state(page)
  const focused = await page.evaluate(
    () => document.activeElement?.getAttribute('aria-label') ?? null,
  )
  if (escaped.expanded !== 'false') fail('Escape did not close the menu')
  else if (!/^Text size/.test(focused ?? '')) fail(`Escape did not return the caret (${focused})`)
  else pass('Escape closes it and returns the caret to the trigger')

  await page.click(TRIGGER)
  await page.mouse.click(300, 400)
  await page.waitForTimeout(200)
  if ((await state(page)).expanded !== 'false') fail('a press outside did not close the menu')
  else pass('a press outside closes it')

  await context.close()
}

// ── 3 · a finger, on a phone ──────────────────────────────────────────────────
{
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  })
  const page = await context.newPage()
  await signIn(page)
  await open(page, { tap: true })
  await page.tap(LARGEST)
  await page.waitForTimeout(400)
  const tapped = await state(page)
  if (tapped.root !== '20px' || tapped.stored !== '125') {
    fail(`a touch tap did not apply the step: ${JSON.stringify(tapped)}`)
  } else {
    pass('a touch tap applies it at 390px')
  }
  await context.close()
}

await browser.close()

console.log(problems.length === 0 ? '\nall gestures work' : `\n${problems.length} problem(s)`)
process.exit(problems.length === 0 ? 0 : 1)
