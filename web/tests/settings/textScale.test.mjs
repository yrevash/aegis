/**
 * Text size — the three claims the feature actually rests on.
 *
 * 1. The step **persists**, and a stored value that is not a step never reaches the DOM.
 * 2. It is **applied before first paint**, which means the inline boot script in the
 *    root layout has to stay faithful to the constants it duplicates. It cannot import
 *    them — it runs in `<head>` before anything is fetched — so a drift between the two
 *    is silent, and this is the only place it can be caught.
 * 3. The default **clears** the property rather than pinning `100%`, so a person who has
 *    already raised their browser's own root size keeps it.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_TEXT_SCALE,
  TEXT_SCALES,
  TEXT_SCALE_BOOT,
  TEXT_SCALE_KEY,
  TEXT_SCALE_PERCENTS,
  applyScale,
  normaliseScale,
  persistScale,
  readStoredScale,
} from '../../src/components/settings/textScale.ts'

/** The smallest thing that behaves like `localStorage`. */
function fakeStorage(initial = {}) {
  const held = { ...initial }
  return {
    held,
    getItem: (key) => (key in held ? held[key] : null),
    setItem: (key, value) => {
      held[key] = String(value)
    },
  }
}

/** A storage that refuses everything — private mode, or storage disabled by policy. */
const hostileStorage = {
  getItem() {
    throw new Error('SecurityError')
  },
  setItem() {
    throw new Error('SecurityError')
  },
}

test('the steps are discrete, ordered, and include the default', () => {
  assert.ok(TEXT_SCALES.length >= 3)
  assert.ok(TEXT_SCALE_PERCENTS.includes(DEFAULT_TEXT_SCALE))
  for (let i = 1; i < TEXT_SCALE_PERCENTS.length; i += 1) {
    assert.ok(TEXT_SCALE_PERCENTS[i] > TEXT_SCALE_PERCENTS[i - 1])
  }
})

test('a chosen step round-trips through storage', () => {
  const storage = fakeStorage()
  persistScale(storage, 125)
  assert.equal(storage.held[TEXT_SCALE_KEY], '125')
  assert.equal(readStoredScale(storage), 125)
})

test('nothing stored means the default, and storage that throws is not a failure', () => {
  assert.equal(readStoredScale(fakeStorage()), DEFAULT_TEXT_SCALE)
  assert.equal(readStoredScale(null), DEFAULT_TEXT_SCALE)
  assert.equal(readStoredScale(hostileStorage), DEFAULT_TEXT_SCALE)
  // A write that cannot be stored still must not throw into the click handler.
  assert.doesNotThrow(() => persistScale(hostileStorage, 110))
  assert.doesNotThrow(() => persistScale(null, 110))
})

test('a value that is not a step never reaches the document', () => {
  // The point of discrete steps is that no layout is ever rendered at a size nobody
  // has looked at. Junk falls back; a near miss snaps to the nearest declared step.
  assert.equal(normaliseScale('nonsense'), DEFAULT_TEXT_SCALE)
  assert.equal(normaliseScale(null), DEFAULT_TEXT_SCALE)
  assert.equal(normaliseScale(Number.NaN), DEFAULT_TEXT_SCALE)
  assert.equal(normaliseScale(Infinity), DEFAULT_TEXT_SCALE)
  assert.equal(normaliseScale('125'), 125)
  assert.equal(normaliseScale(120), 125)
  assert.equal(normaliseScale(1000), TEXT_SCALE_PERCENTS[TEXT_SCALE_PERCENTS.length - 1])
  assert.equal(normaliseScale(-40), TEXT_SCALE_PERCENTS[0])
  assert.equal(readStoredScale(fakeStorage({ [TEXT_SCALE_KEY]: 'drop table' })), DEFAULT_TEXT_SCALE)
})

test('applying a step sets the root font size, and the default clears it', () => {
  const root = { style: { fontSize: '' } }
  applyScale(root, 125)
  assert.equal(root.style.fontSize, '125%')
  applyScale(root, 90)
  assert.equal(root.style.fontSize, '90%')
  // Not `100%`: the browser's own root size is itself an accessibility setting, and
  // pinning it would silently undo somebody who had already raised it.
  applyScale(root, DEFAULT_TEXT_SCALE)
  assert.equal(root.style.fontSize, '')
  assert.doesNotThrow(() => applyScale(null, 125))
})

test('the pre-paint boot script still agrees with the constants it duplicates', () => {
  // It cannot import them — it is inlined in <head> and runs before any module loads —
  // so this is the only thing standing between a renamed key and a setting that
  // silently stops applying until after the first paint.
  assert.match(TEXT_SCALE_BOOT, new RegExp(`'${TEXT_SCALE_KEY}'`))
  assert.ok(TEXT_SCALE_BOOT.includes(`[${TEXT_SCALE_PERCENTS.join(',')}]`))
  assert.ok(TEXT_SCALE_BOOT.includes('document.documentElement.style.fontSize'))
  assert.ok(TEXT_SCALE_BOOT.includes('try{'), 'a throwing storage must not break the page')
  assert.ok(!TEXT_SCALE_BOOT.includes('\n'), 'the inline script must stay a single line')
})

test('the boot script applies exactly what readStoredScale would have', () => {
  // Run the real script against a fake window, for each step and for junk, and assert
  // it lands where the module says it should.
  for (const stored of [...TEXT_SCALE_PERCENTS.map(String), 'nonsense', '', '120']) {
    const root = { style: { fontSize: '' } }
    const scope = {
      localStorage: fakeStorage({ [TEXT_SCALE_KEY]: stored }),
      document: { documentElement: root },
    }
    new Function('localStorage', 'document', TEXT_SCALE_BOOT)(scope.localStorage, scope.document)
    const expected = TEXT_SCALE_PERCENTS.includes(Number.parseInt(stored, 10))
      ? Number.parseInt(stored, 10)
      : DEFAULT_TEXT_SCALE
    assert.equal(
      root.style.fontSize,
      expected === DEFAULT_TEXT_SCALE ? '' : `${expected}%`,
      `stored ${JSON.stringify(stored)} painted at ${root.style.fontSize || 'default'}`,
    )
  }
})
