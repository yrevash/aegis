/**
 * DevOps display logic — the pure, side-effect-free core behind the Tech Stack
 * and Patch Check surfaces.
 *
 * Kept free of React and recharts so it is unit-testable in a plain Node
 * environment (see `stackDisplay.test.ts`) — importing recharts into a `.test.ts`
 * hangs the runner, so all grouping / counting / ordering lives here and the
 * `.tsx` views only format and render.
 *
 * Every function is honest by construction: a missing version is surfaced as
 * "not installed", and an offline patch check can never resolve to "current".
 */

import type { PatchResult, StackComponent } from '@/types/api'

// ── Tech stack (SBOM) ───────────────────────────────────────────────────────

export type StackCategory = StackComponent['category']

/** Canonical rendering order — how the stack is layered, runtime up to infra. */
export const CATEGORY_ORDER: StackCategory[] = ['runtime', 'backend', 'frontend', 'infra']

/** Human labels for each category heading. */
export const CATEGORY_LABEL: Record<StackCategory, string> = {
  runtime: 'Runtime',
  backend: 'Backend',
  frontend: 'Frontend',
  infra: 'Infrastructure',
}

/** One category section: its rows in stable input order. */
export interface StackGroup {
  category: StackCategory
  label: string
  rows: StackComponent[]
}

/**
 * Group components into the canonical category order, dropping any category that
 * has no components so the view never renders an empty section.
 */
export function groupByCategory(components: StackComponent[]): StackGroup[] {
  return CATEGORY_ORDER.map((category) => ({
    category,
    label: CATEGORY_LABEL[category],
    rows: components.filter((c) => c.category === category),
  })).filter((group) => group.rows.length > 0)
}

/** Header KPI band figures for the stack. */
export interface StackSummary {
  /** Total inventoried components. */
  total: number
  /** Components with a resolved version string. */
  withVersion: number
  /** Components whose version could not be determined. */
  unknownVersion: number
  /** Distinct categories actually present. */
  categories: number
}

/** True when a version string is present and non-blank. */
export function hasVersion(version: string | null): boolean {
  return version != null && version.trim() !== ''
}

/** Roll the component list up into the header KPI figures. */
export function summarizeStack(components: StackComponent[]): StackSummary {
  const withVersion = components.filter((c) => hasVersion(c.version)).length
  const categories = new Set(components.map((c) => c.category)).size
  return {
    total: components.length,
    withVersion,
    unknownVersion: components.length - withVersion,
    categories,
  }
}

/** A version rendered honestly: real pins render as-is, absent ones as n-a. */
export interface VersionLabel {
  text: string
  known: boolean
}

/** Map a raw version to its display label; null/blank ⇒ "not installed / n-a". */
export function versionLabel(version: string | null): VersionLabel {
  if (!hasVersion(version)) return { text: 'not installed / n-a', known: false }
  return { text: (version as string).trim(), known: true }
}

// ── Patch check ─────────────────────────────────────────────────────────────

export type PatchStatus = PatchResult['status']

/** Worst-first ordering — updates that need action rise to the top of the table. */
export const PATCH_STATUS_ORDER: PatchStatus[] = ['outdated', 'unknown', 'current']

/** Plain-language status labels (the raw enum reads a little terse in a table). */
export const PATCH_STATUS_LABEL: Record<PatchStatus, string> = {
  current: 'current',
  outdated: 'update available',
  unknown: 'unverified',
}

/** Summary-strip counts for the patch results. */
export interface PatchSummary {
  total: number
  current: number
  outdated: number
  unknown: number
}

/** Tally results by status for the summary strip. */
export function summarizePatches(results: PatchResult[]): PatchSummary {
  return {
    total: results.length,
    current: results.filter((r) => r.status === 'current').length,
    outdated: results.filter((r) => r.status === 'outdated').length,
    unknown: results.filter((r) => r.status === 'unknown').length,
  }
}

/** Sort worst-first (outdated → unknown → current), then alphabetically by name. */
export function sortByStatus(results: PatchResult[]): PatchResult[] {
  const rank = (status: PatchStatus): number => PATCH_STATUS_ORDER.indexOf(status)
  return [...results].sort(
    (a, b) => rank(a.status) - rank(b.status) || a.name.localeCompare(b.name),
  )
}

/**
 * Filter results by a case-insensitive substring of the package name. A blank
 * or whitespace-only query returns every result unchanged.
 */
export function filterByName(results: PatchResult[], query: string): PatchResult[] {
  const needle = query.trim().toLowerCase()
  if (needle === '') return results
  return results.filter((r) => r.name.toLowerCase().includes(needle))
}

/**
 * The overall posture headline. Crucially, an **offline** check is always
 * `unverified` — we never imply the stack is current when the registry could
 * not be reached — and any unresolved package downgrades an otherwise-clean
 * check to `unverified` rather than `current`.
 */
export type PatchPosture = 'action-needed' | 'unverified' | 'current'

/** Derive the posture from the summary + whether the registry was reachable. */
export function patchPosture(summary: PatchSummary, online: boolean): PatchPosture {
  if (!online) return 'unverified'
  if (summary.outdated > 0) return 'action-needed'
  if (summary.unknown > 0) return 'unverified'
  return 'current'
}

/** Short badge label for a posture. */
export const POSTURE_LABEL: Record<PatchPosture, string> = {
  'action-needed': 'updates available',
  unverified: 'unverified',
  current: 'up to date',
}
