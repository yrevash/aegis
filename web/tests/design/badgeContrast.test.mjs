/**
 * Every status badge must be readable on its own fill.
 *
 * A `Badge` sets **12px** text on a 15%-tinted wash of its own hue, which is the
 * one place in this design where ink and background are near-relatives. That is
 * also the place where a hue chosen for the *fill* gets reused for the *text* —
 * and the failure is silent, because a badge whose ink is too light still looks
 * deliberate. It reads as a design choice, not as a defect.
 *
 * It had been failing for five of seven tones. Measured against the composited
 * fill: `graph` 4.12:1, `risk` 3.27:1, `block` 4.39:1, `neutral` 4.37:1, and
 * `ok` at **2.42:1** — a green so light on so light a wash that the word inside
 * it was barely there. The fix was one step deeper on each hue's own scale, so
 * nothing about the palette's identity moved.
 *
 * This test recomputes the ratio from the real token values rather than
 * asserting the hexes, so it fails on the thing that matters — legibility — and
 * stays quiet when a hue is legitimately re-tuned.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..', '..')
const css = readFileSync(join(root, 'src', 'app', 'globals.css'), 'utf8')
const signals = readFileSync(join(root, 'src', 'config', 'signals.ts'), 'utf8')

/** WCAG AA for text below 18.66px bold / 24px regular. Badges are 12px. */
const AA = 4.5

const token = (name) => {
  const m = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))
  assert.ok(m, `token --${name} is not defined in globals.css`)
  return m[1]
}

const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))

const luminance = (hex) => {
  const [r, g, b] = channels(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** A `bg-x/NN` utility composites over the card surface, which is white. */
const compositeOnSurface = (hex, alpha) =>
  `#${channels(hex)
    .map((v) => Math.round(v * alpha + 255 * (1 - alpha)).toString(16).padStart(2, '0'))
    .join('')}`

/**
 * Read the live `SIGNALS` map rather than restating it: a tone added to
 * `signals.ts` without a matching entry here would otherwise ship unmeasured.
 */
const parseSignals = () => {
  const body = signals.slice(signals.indexOf('SIGNALS: Record<Signal, SignalToken> = {'))
  const rows = [...body.matchAll(/^\s{2}(\w+): \{ text: '([^']+)'.*?bg: '([^']+)'/gm)]
  assert.ok(rows.length >= 7, `expected the full signal map, parsed ${rows.length} rows`)
  return rows.map(([, tone, text, bg]) => ({ tone, text, bg }))
}

/** `text-risk-ink` → `--risk-ink`; `text-blue-700` → `--blue-700`. */
const inkFor = (tone, textClass) => {
  // `neutral` is the one tone Badge.tsx deliberately overrides to `--foreground`,
  // because its signal ink is a muted grey that cannot carry 12px on a tint.
  if (tone === 'neutral') return token('foreground')
  const name = textClass.replace(/^text-/, '')
  return token(name)
}

/** `bg-risk/15` → [--risk, 0.15]; `bg-surface-2` → [--surface-2, 1]. */
const fillFor = (bgClass) => {
  const [name, pct] = bgClass.replace(/^bg-/, '').split('/')
  return [token(name), pct ? Number(pct) / 100 : 1]
}

test('every badge tone reads at AA on its own fill', () => {
  const failures = []
  for (const { tone, text, bg } of parseSignals()) {
    const ink = inkFor(tone, text)
    const [fill, alpha] = fillFor(bg)
    const ratio = contrast(ink, compositeOnSurface(fill, alpha))
    if (ratio < AA) failures.push(`${tone}: ${ink} on ${bg} → ${ratio.toFixed(2)}:1`)
  }
  assert.deepEqual(failures, [], `badge tones below ${AA}:1 at 12px:\n  ${failures.join('\n  ')}`)
})

test('a status ink is never the same step as the fill it sits on', () => {
  // The original defect in one line: `--ok-ink` was a *fill*-weight green. If a
  // tone's ink ever equals its own fill token again, the badge is a wash.
  for (const name of ['risk', 'block', 'ok']) {
    assert.notEqual(token(`${name}-ink`), token(name), `--${name}-ink must be deeper than --${name}`)
  }
})
