/**
 * How a catalogue descriptor becomes a screen — with no React and no DOM in sight.
 *
 * Everything a settings screen has to *decide* lives here, and nothing that renders
 * does. That split is the point of the task: the form below is a projection of
 * `aegis.settings.spec.SETTING_SPECS`, so a key added to the catalogue next month
 * appears on the screen with no file in this folder edited. There is no list of keys
 * anywhere in this package — grouping is derived from the key's own namespace, the
 * control comes from the descriptor's `control`/`type`, the help text is the
 * catalogue's own `description`, and the legal values are the ones the server will
 * validate against.
 *
 * Four rules are worth stating because getting any of them wrong makes the screen lie:
 *
 * 1. **`effective: false` outranks everything.** A key that binds to nothing renders as
 *    a value and its `inert_reason`, never as an input. A writable inert key is the
 *    worst case — the write succeeds, the audit row is written, and no run changes —
 *    so {@link fieldFor} checks it *before* it checks `writable`.
 * 2. **`writable_by` decides what is editable**, and an unwritable key renders as a
 *    value with the reason. A disabled-looking input that posts and 403s teaches the
 *    operator that the console is broken rather than that the boundary is real.
 * 3. **The merge rule is part of the value.** A `tighten_only` key and an `override`
 *    key behave in opposite ways when a tenant writes them, and `strictest()` folding
 *    means a write can be accepted and still not be the value in force.
 * 4. **A refusal is the server's sentence.** The resolver refuses precisely — "weaker
 *    than the 'medium' already in force from the enclosing scope" — and that beats any
 *    status table this file could carry.
 *
 * Nothing here reads the network or the clock, so `web/tests/settings/settingsCatalogue.test.mjs`
 * exercises it directly under `node --test`.
 */

import type { SettingControl, SettingRow, SettingScope } from '@/lib/api/console'
import type { FineRole } from '@/lib/api/types'

// ── Sections ─────────────────────────────────────────────────────────────────

/**
 * A human title for a key **namespace** — the part before the first dot.
 *
 * Deliberately keyed on the namespace and not on the key: this is the only place the
 * screen knows anything the catalogue did not tell it, and a namespace it has never
 * seen still gets a section (see {@link sectionTitle}) rather than disappearing. A map
 * from key to label would be the hand-written second catalogue this whole task exists
 * to prevent.
 */
const SECTION_TITLES: Readonly<Record<string, string>> = {
  agent: 'How the agent answers',
  guardrails: 'Guardrails',
  jobs: 'Ingestion jobs',
  memory: 'Memory and retention',
}

/** The order the known namespaces read in; anything else follows, alphabetically. */
const SECTION_ORDER: readonly string[] = Object.keys(SECTION_TITLES)

/** One group of controls, as the screen draws it. */
export interface SettingsSection {
  /** The key namespace this section collects — `agent`, `guardrails`, … */
  id: string
  /** The heading. */
  title: string
  /** The controls in it, in the order the server sent them. */
  rows: SettingRow[]
}

/** The namespace of a catalogue key: everything before the first dot. */
export function namespaceOf(key: string): string {
  const dot = key.indexOf('.')
  return dot === -1 ? key : key.slice(0, dot)
}

/** Sentence-case a dotted/underscored fragment: `max_plan_iterations` → `Max plan iterations`. */
function humanise(fragment: string): string {
  const words = fragment.replace(/[._-]+/g, ' ').trim()
  if (words === '') return ''
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/**
 * The heading for a namespace — the declared one, or one derived from the namespace.
 *
 * The fallback is the load-bearing half. A key in a namespace nobody anticipated still
 * lands in a titled section, so adding `seat.can_approve` to the catalogue produces a
 * "Seat" section with no frontend change.
 */
export function sectionTitle(namespace: string): string {
  return SECTION_TITLES[namespace] ?? humanise(namespace)
}

/**
 * The label for one control, derived from its key.
 *
 * The catalogue has no `label` field, so this is the honest derivation rather than a
 * hand-kept table: strip the namespace the section already names, then humanise what is
 * left. `agent.team.max_parallel` in the agent section reads "Team max parallel". The
 * exact key is rendered beside it, so nothing is lost when the derivation reads oddly.
 */
export function controlLabel(key: string): string {
  const dot = key.indexOf('.')
  return humanise(dot === -1 ? key : key.slice(dot + 1))
}

/**
 * Group the resolved rows into the sections the page draws, losing none of them.
 *
 * Every row lands in exactly one section — a key whose namespace is unknown gets its
 * own, which is what makes "a new catalogue key needs no frontend change" true rather
 * than aspirational.
 *
 * @param rows - The rows from `GET /settings`, in the server's order.
 * @returns Sections, known namespaces first in their declared order, then the rest
 *   alphabetically.
 */
export function groupSettings(rows: readonly SettingRow[]): SettingsSection[] {
  const byNamespace = new Map<string, SettingRow[]>()
  for (const row of rows) {
    const id = namespaceOf(row.key)
    const bucket = byNamespace.get(id)
    if (bucket === undefined) byNamespace.set(id, [row])
    else bucket.push(row)
  }
  const rank = (id: string): number => {
    const index = SECTION_ORDER.indexOf(id)
    return index === -1 ? SECTION_ORDER.length : index
  }
  return [...byNamespace.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([id, group]) => ({ id, title: sectionTitle(id), rows: group }))
}

// ── Which control, and whether it is a control at all ────────────────────────

/** What the screen draws for one row. `inert` and `readOnly` are not inputs. */
export type SettingField =
  /** The key binds to nothing yet: show the value and say so. */
  | { kind: 'inert'; reason: string }
  /** The caller may read it and not write it: show the value and say who may. */
  | { kind: 'readOnly'; reason: string }
  | { kind: 'select'; choices: readonly unknown[] }
  | { kind: 'toggle' }
  | { kind: 'number'; minimum?: number; maximum?: number; step: number }
  | { kind: 'tags' }
  | { kind: 'text' }

/** How each fine role is named in a sentence about who may write something. */
const ROLE_LABELS: Readonly<Record<string, string>> = {
  platform_admin: 'a platform admin',
  tenant_admin: 'a tenant admin',
  ai_team: 'the AI team',
  devops: 'DevOps',
  client: 'a client',
}

/** The RBAC ladder, most privileged first — the order roles are listed in prose. */
const ROLE_ORDER: readonly string[] = ['platform_admin', 'tenant_admin', 'ai_team', 'devops', 'client']

/** Name one fine role for a sentence; an unknown tier is printed as it arrived. */
export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

/** Join a list into prose: `a, b and c`. */
function andList(parts: readonly string[]): string {
  if (parts.length === 0) return 'nobody'
  if (parts.length === 1) return parts[0]
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/**
 * Why this caller may not write a key they can see.
 *
 * Reads `writable_by` off the descriptor rather than restating a rule, so the sentence
 * stays true when the catalogue changes who owns a control.
 */
export function readOnlyReason(control: SettingControl): string {
  // An unknown tier sorts last rather than first, so a role this build has not met
  // cannot quietly lead the sentence.
  const rank = (role: string): number => {
    const index = ROLE_ORDER.indexOf(role)
    return index === -1 ? ROLE_ORDER.length : index
  }
  const roles = [...control.writable_by].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
  return `Only ${andList(roles.map(roleLabel))} may change this. You can see what is in force.`
}

/** What an inert key says about itself — the catalogue's reason, or a floor. */
export function inertReason(control: SettingControl): string {
  const declared = control.inert_reason?.trim()
  if (declared !== undefined && declared !== '') return declared
  return 'Nothing reads this setting yet, so changing it would not affect a run.'
}

/**
 * What to draw for one row: an input, or a value with a reason.
 *
 * The order of the two refusals is the whole rule. `effective: false` is checked
 * **first**, because the dangerous case is a key that is inert *and* writable: the
 * write is accepted, audited, resolved and reaches nothing, which is exactly what
 * `agent.gate_min_risk` did for a phase. An inert control is never an input, whoever
 * is looking at it.
 */
export function fieldFor(row: SettingRow): SettingField {
  const control = row.control
  if (control.effective === false) return { kind: 'inert', reason: inertReason(control) }
  if (!row.writable) return { kind: 'readOnly', reason: readOnlyReason(control) }
  if (control.control === 'select' && Array.isArray(control.choices)) {
    return { kind: 'select', choices: control.choices }
  }
  if (control.type === 'bool' || control.control === 'toggle') return { kind: 'toggle' }
  if (control.control === 'number' || control.type === 'int' || control.type === 'float') {
    return {
      kind: 'number',
      minimum: control.minimum,
      maximum: control.maximum,
      step: control.type === 'int' ? 1 : 0.01,
    }
  }
  if (control.control === 'tags' || control.type === 'list') return { kind: 'tags' }
  // A control kind this build has never met still renders as a text box rather than
  // vanishing: the server validates the value and refuses with its own sentence, which
  // is a better failure than a key the operator cannot see at all.
  return { kind: 'text' }
}

/** Whether a field is one the operator can type into. */
export function isEditable(field: SettingField): boolean {
  return field.kind !== 'inert' && field.kind !== 'readOnly'
}

// ── Provenance and the merge rule ────────────────────────────────────────────

/** How a value's deciding scope is presented. */
export interface Provenance {
  /** The badge. */
  label: string
  /** The badge tone, from the shared signal palette. */
  tone: 'neutral' | 'graph' | 'ok'
  /** One sentence saying what that scope means for this reader. */
  detail: string
}

const PROVENANCE: Readonly<Record<string, Provenance>> = {
  platform: {
    label: 'Platform default',
    tone: 'neutral',
    detail: 'Aegis decides this for every tenant; nobody in your tenant has changed it.',
  },
  tenant: {
    label: 'Your tenant',
    tone: 'graph',
    detail: 'Your tenant set this for everyone in it, inside what the platform allows.',
  },
  user: {
    label: 'Your setting',
    tone: 'ok',
    detail: 'You set this for yourself, inside what your tenant and the platform allow.',
  },
}

/** Which scope decided this value, said in a way an operator can act on. */
export function provenanceOf(row: SettingRow): Provenance {
  return (
    PROVENANCE[row.source] ?? {
      label: row.source,
      tone: 'neutral',
      detail: 'The server named this scope as the one that decided the value.',
    }
  )
}

/**
 * What the merge rule means for whoever is about to change this key.
 *
 * The sentence a `tighten_only` key needs and an `override` key must not get: one can
 * be made stricter and never weaker by anyone below the platform, and the other is
 * simply the narrowest scope's choice. Rendering them identically is how an operator
 * comes to believe a write took when the fold discarded it.
 */
export function mergeNote(control: SettingControl): string {
  if (control.merge === 'tighten_only') {
    const direction =
      control.stricter === 'higher_is_stricter'
        ? 'a higher value is the stricter one'
        : 'a lower value is the stricter one'
    return `Tighten only — ${direction}. Your scope may make it stricter and never weaker; a weakening write is refused with a reason.`
  }
  if (control.merge === 'union') {
    return 'Additive — what you add joins the platform’s entries. Nothing already in force can be removed here.'
  }
  if (control.merge === 'override') {
    return 'Override — the narrowest scope that sets it wins, in either direction.'
  }
  return `Merged by the “${control.merge}” rule the catalogue declares for this key.`
}

/** Whether a scope below the platform may make this key weaker. */
export function canWeaken(control: SettingControl): boolean {
  return control.merge !== 'tighten_only' && control.merge !== 'union'
}

// ── Values on and off the wire ───────────────────────────────────────────────

/** Render any resolved value for display, whatever type the catalogue declared. */
export function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? 'none' : value.join(', ')
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return value === null || value === undefined || value === '' ? 'none' : String(value)
}

/** The text a control starts out holding for a value. */
export function draftOf(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ')
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return value === null || value === undefined ? '' : String(value)
}

/**
 * Parse what a control produced back into the type the catalogue declared.
 *
 * Deliberately no clamping and no rounding. An out-of-range value goes to the server
 * and comes back refused with the bound it broke, which is more use to an operator than
 * silently becoming a different number — and it keeps the bounds enforced in exactly
 * one place.
 */
export function parseValue(raw: string, control: SettingControl): unknown {
  if (control.type === 'bool') return raw === 'true'
  if (control.type === 'int') return Number.parseInt(raw, 10)
  if (control.type === 'float') return Number.parseFloat(raw)
  if (control.type === 'list') {
    return raw
      .split(',')
      .map((part) => part.trim())
      .filter((part) => part.length > 0)
  }
  return raw
}

/** Structural equality for setting values; lists compare as sets, because unions are. */
function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false
    const other = b.map((entry) => JSON.stringify(entry))
    return a.every((entry) => other.includes(JSON.stringify(entry)))
  }
  return JSON.stringify(a) === JSON.stringify(b)
}

// ── What a write actually did ────────────────────────────────────────────────

/** How a scope is named in a sentence about where a value came from. */
const SCOPE_NAMES: Readonly<Record<string, string>> = {
  platform: 'the platform default',
  tenant: 'your tenant',
  user: 'your own preference',
}

/** Name one scope for prose. */
function scopeName(scope: string): string {
  return SCOPE_NAMES[scope] ?? scope
}

/** The result of an accepted write, as the operator needs to read it. */
export interface WriteOutcome {
  /**
   * Whether the value now in force is the value that was typed. `false` is not a
   * failure — the server accepted the write — it means the fold decided something else,
   * and saying so is the difference between a screen that reports and one that flatters.
   */
  took: boolean
  /** The sentence to render under the control. */
  sentence: string
}

/**
 * What to tell the operator after `PUT /settings/{key}` succeeded.
 *
 * Rendered from the **response**, never from the request, because `strictest()` and the
 * union fold mean the two can differ. Three outcomes, and the last one is the one this
 * function exists for:
 *
 * - the write decided the value, at the scope it was written to;
 * - the write was stored and something else still decides the value;
 * - the value in force is **not what was typed**, because the fold combined it with an
 *   enclosing scope — a tenant adding one PII entity gets the platform's three back
 *   beside it, and a platform admin loosening a key a tenant already tightened gets the
 *   tenant's value back. Both are correct, and both look like a bug when hidden.
 *
 * @param submitted - The value that was sent.
 * @param scope - The layer it was written at.
 * @param row - The re-resolved row the server answered with.
 */
export function writeOutcome(
  submitted: unknown,
  scope: SettingScope,
  row: SettingRow,
): WriteOutcome {
  const inForce = formatValue(row.value)
  if (!sameValue(submitted, row.value)) {
    const why =
      row.control.merge === 'union'
        ? 'your entries were merged with the ones already in force'
        : 'a stricter value from an enclosing scope still wins'
    return {
      took: false,
      sentence: `Saved at ${scopeName(scope)}, but the value in force is ${inForce}, decided by ${scopeName(row.source)} — ${why}.`,
    }
  }
  if (row.source !== scope) {
    return {
      took: true,
      sentence: `Saved at ${scopeName(scope)}. The value in force is ${inForce}, still decided by ${scopeName(row.source)}, which already had it there.`,
    }
  }
  return { took: true, sentence: `Saved. The value in force is ${inForce}, decided by ${scopeName(scope)}.` }
}

/**
 * The sentence to render for a refusal.
 *
 * The server's own reason wins whenever it sent one — `ConsoleApiError.message` is the
 * resolver's sentence, and "weaker than the 'medium' already in force from the
 * enclosing scope" is the entire explanation an operator needs. The fallback exists for
 * a failure that never reached the backend.
 */
export function refusalSentence(error: unknown): string {
  if (error instanceof Error && error.message.trim() !== '') return error.message
  return 'That change did not reach the backend, so nothing was saved. Check it is up, then retry.'
}

// ── Which layer a write may target ───────────────────────────────────────────

/** One writable layer, or the reason this caller cannot reach it. */
export interface ScopeOption {
  id: SettingScope
  label: string
  /** Whether this caller may write at this layer at all. */
  available: boolean
  /** When unavailable, why — rendered instead of a control that would 403. */
  reason?: string
}

/** The RBAC rank of a fine tier, mirroring `aegis.governance.config.role_rank`. */
const RANK: Readonly<Record<string, number>> = {
  platform_admin: 4,
  tenant_admin: 3,
  ai_team: 2,
  devops: 2,
  client: 1,
}

/**
 * Which of the three layers this caller may write at, and why not for the rest.
 *
 * A mirror of `aegis.settings.resolver._check_scope_permission`, and mirrored for the
 * same reason `adminForms.ts` mirrors the admin guards: so the screen can say *not
 * yours, because* rather than offer a control the backend will refuse. The server stays
 * the authority — every refusal below still happens there, with its own sentence — and
 * the two ids that decide it (`tenant_id`, `user_id`) are the ones `GET /settings`
 * reports off the token rather than anything guessed here.
 *
 * @param fineRole - The caller's fine tier.
 * @param tenantId - The tenant the token resolves to, or null for a platform principal.
 * @param userId - The caller's user id, or null for a principal with no user row.
 */
export function writableScopes(
  fineRole: FineRole | null | undefined,
  tenantId: number | null,
  userId: number | null,
): ScopeOption[] {
  const rank = fineRole == null ? 0 : (RANK[fineRole] ?? 0)
  const option = (
    id: SettingScope,
    label: string,
    available: boolean,
    reason: string,
  ): ScopeOption => (available ? { id, label, available } : { id, label, available, reason })

  return [
    option(
      'user',
      'Just me',
      tenantId !== null && userId !== null,
      tenantId === null
        ? 'A preference is stored inside a tenant, and this sign-in is not bound to one.'
        : 'This sign-in carries no user identity, so it has no preference of its own.',
    ),
    option(
      'tenant',
      'Everyone in my tenant',
      tenantId !== null && rank >= RANK.ai_team,
      tenantId === null
        ? 'This sign-in spans every tenant, so it has no single tenant to set a default for.'
        : 'Setting your tenant’s default is an operator’s to do; yours applies only to you.',
    ),
    option(
      'platform',
      'Every tenant',
      rank >= RANK.platform_admin,
      'The platform default is the platform admin’s to set.',
    ),
  ]
}
