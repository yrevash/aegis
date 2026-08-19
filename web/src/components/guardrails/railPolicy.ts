/**
 * The pure half of the rail-policy screen: naming, formatting, and provenance.
 *
 * Separated from the component so the one claim that matters can be tested without a
 * DOM: **provenance is reported, never inferred.** The server decides whether a value
 * is the platform's floor or this tenant's tightening — by comparing the folded policy
 * against the floor, which is the only place both are known — and this module's job is
 * to say so in words. A screen that re-derived it from the values it happened to
 * receive would be a second policy, and the day the two disagreed the badge would be
 * the one lying.
 */

import type { GuardrailControl } from '@/lib/api/guardrails'

/**
 * Short human names for the controls, falling back to the key itself.
 *
 * The fallback is the point: a control added to the catalogue tomorrow renders the day
 * it is declared, named by its key rather than vanishing because this map was not
 * updated. Nothing about behaviour is decided here.
 */
const LABELS: Record<string, string> = {
  'guardrails.topical.block': 'Off-topic questions',
  'guardrails.grounding.block': 'Unsupported answers',
  'guardrails.denylist.terms': 'Denied terms',
  'guardrails.denylist.patterns': 'Screened patterns',
  'guardrails.pii.entities': 'Extra personal-data kinds',
  'guardrails.pii.block': 'Personal data',
  'guardrails.input.max_chars': 'Longest question accepted',
}

/** The human name for a control key, or the key itself when it is new here. */
export function controlName(key: string): string {
  return LABELS[key] ?? key
}

/** Render one setting value in the shape its type actually has. */
export function formatValue(value: unknown, type: string): string {
  if (type === 'bool') return value ? 'on' : 'off'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none'
  return String(value)
}

/** What the provenance badge says, and whether it marks the tenant's own change. */
export interface Provenance {
  label: string
  /** True when this tenant's own write is what is in force. */
  mine: boolean
}

/**
 * Describe where a control's effective value came from.
 *
 * Reads the server's ``source`` and nothing else. A tenant row that lost to a stricter
 * platform value comes back as ``platform`` — it is not in force — and this must keep
 * saying "platform floor" for it, because "your setting" over a value the tenant cannot
 * change is exactly the badge that made four dead controls look alive.
 */
export function provenanceOf(row: Pick<GuardrailControl, 'source'>): Provenance {
  if (row.source === 'platform') return { label: 'platform floor', mine: false }
  return { label: `your ${row.source} setting`, mine: true }
}

/** The members this tenant added on top of the floor, for a union control. */
export function addedMembers(row: Pick<GuardrailControl, 'added'>): unknown[] {
  return row.added ?? []
}
