/**
 * The rules a generated settings screen is not allowed to get wrong.
 *
 * The claim under test is the one the whole task rests on: **the screen is a projection
 * of `aegis.settings.spec.SETTING_SPECS`, not a second copy of it.** A hand-written form
 * that happens to match today drifts tomorrow, so two of these tests are written to fail
 * if a per-key list ever appears — one feeds the module a key nobody has ever declared
 * and asserts it renders like any other, and one reads the components' own source and
 * fails on a hard-coded catalogue key.
 *
 * The rest are the three ways a settings screen lies: rendering an inert control as
 * live, offering an input the server will refuse, and reporting a write as though the
 * fold took the typed value when it did not.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  canWeaken,
  controlLabel,
  fieldFor,
  formatValue,
  groupSettings,
  isEditable,
  mergeNote,
  parseValue,
  provenanceOf,
  readOnlyReason,
  refusalSentence,
  sectionTitle,
  writableScopes,
  writeOutcome,
} from '../../src/components/settings/settingsCatalogue.ts'

/** A control descriptor shaped exactly as `setting_controls()` emits one. */
function control(overrides = {}) {
  return {
    key: 'agent.max_plan_iterations',
    type: 'int',
    control: 'number',
    default: 2,
    merge: 'tighten_only',
    writable_by: ['ai_team', 'platform_admin', 'tenant_admin'],
    readable_by: ['ai_team', 'client', 'devops', 'platform_admin', 'tenant_admin'],
    description: 'Hard cap on planning rounds per run.',
    effective: true,
    minimum: 1,
    maximum: 10,
    stricter: 'lower_is_stricter',
    ...overrides,
  }
}

/** A resolved row as `GET /settings` returns one. */
function row(overrides = {}, controlOverrides = {}) {
  const spec = control({ ...controlOverrides, ...(overrides.key ? { key: overrides.key } : {}) })
  return {
    key: spec.key,
    value: 2,
    source: 'platform',
    writable: true,
    control: spec,
    ...overrides,
  }
}

// ── The projection: a key nobody wrote frontend code for ─────────────────────

test('a catalogue key this build has never seen still renders as a full control', () => {
  // Phase 7.8's seat toggles, as they would arrive the day somebody adds them to
  // SETTING_SPECS: a namespace with no section title, a boolean, writable. Nothing in
  // `web/` knows this key exists.
  const unseen = row(
    { key: 'seat.can_approve', value: false, source: 'tenant' },
    {
      key: 'seat.can_approve',
      type: 'bool',
      control: 'toggle',
      default: false,
      merge: 'override',
      stricter: undefined,
      minimum: undefined,
      maximum: undefined,
      description: 'Whether this seat may resolve approval gates the tenant owns.',
    },
  )

  const sections = groupSettings([unseen])
  assert.equal(sections.length, 1, 'an unknown namespace must still get a section')
  assert.equal(sections[0].id, 'seat')
  assert.equal(sections[0].title, 'Seat', 'the heading is derived, not looked up')
  assert.deepEqual(sections[0].rows, [unseen], 'the row must not be dropped on the way')

  assert.equal(controlLabel('seat.can_approve'), 'Can approve')
  const field = fieldFor(unseen)
  assert.equal(field.kind, 'toggle', 'the control comes from the descriptor, not from a table')
  assert.ok(isEditable(field), 'a writable, effective key is editable with no frontend change')
  assert.equal(provenanceOf(unseen).label, 'Your tenant')
  assert.ok(mergeNote(unseen.control).startsWith('Override'))
})

test('every row lands in exactly one section, whatever its namespace', () => {
  const rows = [
    row({ key: 'agent.mode' }),
    row({ key: 'guardrails.pii.entities' }),
    row({ key: 'seat.label' }),
    row({ key: 'agent.model' }),
    row({ key: 'nonamespace' }),
  ]
  const sections = groupSettings(rows)
  const placed = sections.flatMap((section) => section.rows.map((r) => r.key))
  assert.equal(placed.length, rows.length, 'no row may be dropped or duplicated')
  assert.deepEqual(new Set(placed), new Set(rows.map((r) => r.key)))
  // Known namespaces lead, in their declared order; the rest follow alphabetically.
  assert.deepEqual(
    sections.map((s) => s.id),
    ['agent', 'guardrails', 'nonamespace', 'seat'],
  )
  assert.equal(sectionTitle('agent'), 'How the agent answers')
})

test('no settings component carries a hard-coded catalogue key', () => {
  // The regression this whole task exists to prevent, checked the only way that keeps
  // working: read the source. A dotted lowercase key literal in this package means
  // somebody started a second catalogue in the browser.
  const dir = fileURLToPath(new URL('../../src/components/settings/', import.meta.url))
  const files = readdirSync(dir).filter((name) => statSync(join(dir, name)).isFile())
  assert.ok(files.length >= 2, `the settings package scan came back near-empty (${files.length})`)

  const offenders = []
  for (const name of files) {
    const source = readFileSync(join(dir, name), 'utf8')
    for (const [index, line] of source.split('\n').entries()) {
      // Prose and doc comments name keys freely; only code may not.
      const code = line
        .replace(/\/\*.*?\*\//g, '')
        .replace(/^\s*(\/\*|\*).*$/, '')
        .replace(/\/\/.*$/, '')
      if (/['"`][a-z]+(?:\.[a-z_]+)+['"`]/.test(code)) {
        offenders.push(`${name}:${index + 1}: ${line.trim()}`)
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    'a settings component must never name a catalogue key — every key comes off the wire',
  )
})

// ── effective: false ─────────────────────────────────────────────────────────

test('an inert control is never an input, even for a role that may write it', () => {
  const inert = row(
    { key: 'agent.mode', value: 'standard', writable: true },
    {
      key: 'agent.mode',
      type: 'str',
      control: 'select',
      choices: ['fast', 'standard', 'team'],
      merge: 'override',
      effective: false,
      inert_reason: 'Nothing reads this yet. The run’s width comes from QueryRequest.depth_mode.',
    },
  )
  const field = fieldFor(inert)
  assert.equal(field.kind, 'inert', 'effective:false outranks writable — otherwise the write reaches nothing')
  assert.ok(!isEditable(field))
  assert.match(field.reason, /Nothing reads this yet/, "the catalogue's own reason is rendered, not paraphrased")
})

test('an inert control with no declared reason still says it is inert', () => {
  const field = fieldFor(row({}, { effective: false }))
  assert.equal(field.kind, 'inert')
  assert.match(field.reason, /would not affect a run/)
})

// ── writable_by ──────────────────────────────────────────────────────────────

test('a key the caller may read but not write renders as a value with the reason', () => {
  const platformOnly = row(
    { key: 'jobs.max_inflight.ingest', writable: false },
    { key: 'jobs.max_inflight.ingest', writable_by: ['platform_admin'] },
  )
  const field = fieldFor(platformOnly)
  assert.equal(field.kind, 'readOnly', 'never a disabled-looking input that posts and 403s')
  assert.ok(!isEditable(field))
  assert.equal(
    field.reason,
    'Only a platform admin may change this. You can see what is in force.',
  )
  // The sentence is read off writable_by, so it stays true when ownership moves.
  assert.match(
    readOnlyReason(control({ writable_by: ['tenant_admin', 'platform_admin'] })),
    /a platform admin and a tenant admin/,
  )
})

// ── Provenance and the merge rule ────────────────────────────────────────────

test('a tighten-only key and an override key do not read the same', () => {
  const tighten = mergeNote(control({ merge: 'tighten_only', stricter: 'lower_is_stricter' }))
  assert.match(tighten, /Tighten only/)
  assert.match(tighten, /lower value is the stricter one/)
  assert.match(tighten, /never weaker/)
  assert.equal(canWeaken(control({ merge: 'tighten_only' })), false)

  assert.match(
    mergeNote(control({ merge: 'tighten_only', stricter: 'higher_is_stricter' })),
    /higher value is the stricter one/,
  )
  assert.match(mergeNote(control({ merge: 'union' })), /Additive/)
  assert.equal(canWeaken(control({ merge: 'union' })), false)
  assert.match(mergeNote(control({ merge: 'override' })), /Override/)
  assert.equal(canWeaken(control({ merge: 'override' })), true)
})

test('each source scope is named as something an operator can act on', () => {
  assert.equal(provenanceOf(row({ source: 'platform' })).label, 'Platform default')
  assert.equal(provenanceOf(row({ source: 'tenant' })).label, 'Your tenant')
  assert.equal(provenanceOf(row({ source: 'user' })).label, 'Your setting')
  // A scope this build does not know is printed, not swallowed.
  assert.equal(provenanceOf(row({ source: 'fleet' })).label, 'fleet')
})

// ── What a write actually did ────────────────────────────────────────────────

test('a union fold that did not take the typed value says so', () => {
  // A tenant adds one PII entity and gets the platform's floor back beside it. The
  // write was accepted; the value in force is not the value typed.
  const answered = row(
    { key: 'guardrails.pii.entities', value: ['EMAIL_ADDRESS', 'PHONE_NUMBER', 'IBAN'], source: 'tenant' },
    { key: 'guardrails.pii.entities', type: 'list', control: 'tags', merge: 'union' },
  )
  const outcome = writeOutcome(['IBAN'], 'tenant', answered)
  assert.equal(outcome.took, false, 'the fold did not take the typed value')
  assert.match(outcome.sentence, /value in force is EMAIL_ADDRESS, PHONE_NUMBER, IBAN/)
  assert.match(outcome.sentence, /merged with the ones already in force/)
})

test('a platform write that a tenant already tightened past reports the tenant’s value', () => {
  const answered = row({ value: 3, source: 'tenant' })
  const outcome = writeOutcome(8, 'platform', answered)
  assert.equal(outcome.took, false)
  assert.match(outcome.sentence, /value in force is 3, decided by your tenant/)
  assert.match(outcome.sentence, /stricter value from an enclosing scope still wins/)
})

test('a write that matched what was already in force is not reported as a change', () => {
  const outcome = writeOutcome(2, 'user', row({ value: 2, source: 'platform' }))
  assert.equal(outcome.took, true)
  assert.match(outcome.sentence, /still decided by the platform default/)
})

test('a write that decided the value says which layer now owns it', () => {
  const outcome = writeOutcome(1, 'tenant', row({ value: 1, source: 'tenant' }))
  assert.equal(outcome.took, true)
  assert.match(outcome.sentence, /decided by your tenant/)
})

test('a list written in another order is not reported as a fold', () => {
  const answered = row({ value: ['b', 'a'], source: 'tenant' }, { type: 'list', merge: 'union' })
  assert.equal(writeOutcome(['a', 'b'], 'tenant', answered).took, true)
})

// ── Refusals ─────────────────────────────────────────────────────────────────

test('a refusal renders as the server’s own sentence', () => {
  const refused = new Error(
    "'high' is weaker than the 'medium' already in force from the enclosing scope",
  )
  assert.equal(refusalSentence(refused), refused.message)
  // Only a failure that carried no sentence at all falls back.
  assert.match(refusalSentence(null), /did not reach the backend/)
  assert.match(refusalSentence(new Error('  ')), /did not reach the backend/)
})

// ── Values, and which layer a write may reach ────────────────────────────────

test('values survive the trip to a control and back in the declared type', () => {
  assert.equal(parseValue('3', control({ type: 'int' })), 3)
  assert.equal(parseValue('0.25', control({ type: 'float' })), 0.25)
  assert.equal(parseValue('true', control({ type: 'bool' })), true)
  assert.equal(parseValue('off', control({ type: 'bool' })), false)
  assert.deepEqual(parseValue('a, b ,, c', control({ type: 'list' })), ['a', 'b', 'c'])
  // No clamping: an out-of-bounds value goes to the server and comes back refused with
  // the bound it broke, so the bounds stay enforced in exactly one place.
  assert.equal(parseValue('99', control({ type: 'int', maximum: 10 })), 99)

  assert.equal(formatValue([]), 'none')
  assert.equal(formatValue(false), 'off')
  assert.equal(formatValue(0), '0')
})

test('a caller is only offered the layers their token can reach', () => {
  const reach = (role, tenantId, userId) =>
    Object.fromEntries(writableScopes(role, tenantId, userId).map((s) => [s.id, s.available]))

  // A platform principal has no tenant, so it has no tenant default and no preference.
  assert.deepEqual(reach('platform_admin', null, 7), {
    user: false,
    tenant: false,
    platform: true,
  })
  assert.deepEqual(reach('tenant_admin', 3, 9), { user: true, tenant: true, platform: false })
  assert.deepEqual(reach('client', 3, 9), { user: true, tenant: false, platform: false })
  // Fails closed on a tier this build has never met.
  assert.deepEqual(reach(null, 3, 9), { user: true, tenant: false, platform: false })

  for (const option of writableScopes('client', 3, 9)) {
    if (!option.available) assert.ok(option.reason.length > 0, `${option.id} needs a reason`)
  }
})
