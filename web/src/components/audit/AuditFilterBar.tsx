'use client'

import { RotateCcw, Search, SlidersHorizontal, X } from 'lucide-react'
import { useId, useState, type ReactElement, type ReactNode } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import type { Tenant } from '@/lib/api/types'
import { cn } from '@/lib/utils'

import {
  EMPTY_AUDIT_QUERY,
  activeFilters,
  isFiltered,
  localSinceHoursAgo,
  type AuditQuery,
} from './query'

interface AuditFilterBarProps {
  value: AuditQuery
  onChange: (next: AuditQuery) => void
  /** Tenants the caller may select between — empty for everyone but a platform admin. */
  tenants?: Tenant[]
  /** Whether a fetch is in flight, so the strip can say the numbers are moving. */
  busy?: boolean
}

/** The three questions actually asked of a trail, as one press each. */
const RANGES: Array<{ label: string; hours: number }> = [
  { label: 'Last hour', hours: 1 },
  { label: '24 hours', hours: 24 },
  { label: '7 days', hours: 24 * 7 },
]

/**
 * The audit filter strip — actor, action family, model, outcome, free text, time range,
 * and (platform admin only) the tenant selector.
 *
 * Every control here changes a **server** query. That is the point of §7.11 and the
 * reason the strip is a form rather than a set of chips over a fetched array: the
 * question an operator asks ("what did this actor do last week") is about the trail, and
 * only the database can answer it.
 *
 * It used to present all eight predicates at once, in two rows of labelled controls, and
 * that had two costs. The strip was **the tallest thing above the table** — a screen
 * whose subject is 2,900 rows opened on a form. And it could hold a filter without ever
 * *saying* it held one: an operator looking at four rows could not tell whether the trail
 * was quiet or whether a stale actor was still applied, because the control carrying it
 * was in the row that had scrolled away.
 *
 * So: one line of controls that answers the common question, three presets for the time
 * range, the remaining five behind a disclosure — and, always visible, a chip per active
 * predicate that removes just that one. The prose that explained prefix matching and the
 * exact-actor rule is in the tips beside the fields, per DESIGN.md §4.
 */
export function AuditFilterBar({
  value,
  onChange,
  tenants = [],
  busy = false,
}: AuditFilterBarProps): ReactElement {
  const id = useId()
  const [advanced, setAdvanced] = useState(false)
  const set = <K extends keyof AuditQuery>(key: K, next: AuditQuery[K]): void => {
    onChange({ ...value, [key]: next })
  }
  const patch = (next: Partial<AuditQuery>): void => onChange({ ...value, ...next })

  const tenantName = (tenantId: number): string | undefined =>
    tenants.find((t) => t.id === tenantId)?.name
  const chips = activeFilters(value, tenantName)
  const filtered = isFiltered(value)

  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-border bg-surface-2/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <label htmlFor={`${id}-text`} className="sr-only">
            Search the trail
          </label>
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground/60"
          />
          <input
            id={`${id}-text`}
            value={value.text}
            onChange={(e) => set('text', e.target.value)}
            placeholder="Search action, actor, model, trace…"
            autoComplete="off"
            spellCheck={false}
            className={cn(INPUT, 'h-9 w-full pl-8')}
          />
        </div>

        <div className="flex items-center gap-1" role="group" aria-label="Time range">
          {RANGES.map((range) => {
            const on = value.since === localSinceHoursAgo(range.hours)
            return (
              <button
                key={range.hours}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  patch(
                    on
                      ? { since: '', until: '' }
                      : { since: localSinceHoursAgo(range.hours), until: '' },
                  )
                }
                className={cn(
                  'h-9 touch-manipulation rounded-full border px-3 text-xs font-medium transition-colors duration-[--dur-fast]',
                  FOCUS,
                  on
                    ? 'border-blue-600 bg-blue-50 text-blue-700'
                    : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
                )}
              >
                {range.label}
              </button>
            )
          })}
        </div>

        <label htmlFor={`${id}-outcome`} className="sr-only">
          Outcome
        </label>
        <select
          id={`${id}-outcome`}
          value={value.outcome ?? ''}
          onChange={(e) =>
            set(
              'outcome',
              e.target.value === '' ? null : (e.target.value as 'blocked' | 'completed'),
            )
          }
          className={cn(INPUT, 'h-9 w-32')}
        >
          <option value="">Any outcome</option>
          <option value="completed">Completed</option>
          <option value="blocked">Blocked</option>
        </select>

        <label htmlFor={`${id}-limit`} className="sr-only">
          Rows per page
        </label>
        <select
          id={`${id}-limit`}
          value={String(value.limit)}
          onChange={(e) => set('limit', Number(e.target.value))}
          className={cn(INPUT, 'h-9 w-[5.5rem]')}
        >
          {[50, 100, 200].map((n) => (
            <option key={n} value={n}>
              {n} rows
            </option>
          ))}
        </select>

        <button
          type="button"
          aria-expanded={advanced}
          onClick={() => setAdvanced((open) => !open)}
          className={cn(
            'inline-flex h-9 touch-manipulation items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition-colors duration-[--dur-fast]',
            FOCUS,
            advanced
              ? 'border-blue-600 bg-blue-50 text-blue-700'
              : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
          )}
        >
          <SlidersHorizontal aria-hidden className="size-3.5" />
          More
        </button>

        {busy ? (
          <span aria-live="polite" className="font-mono text-[0.6875rem] text-muted-foreground">
            Loading…
          </span>
        ) : null}
      </div>

      {advanced ? (
        <div className="grid gap-3 border-t border-border pt-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field
            label="Actor"
            htmlFor={`${id}-actor`}
            tip="Matched exactly, not as a substring — a trail search that quietly widened would be evidence of the wrong thing. Use the search box for a partial name."
          >
            <input
              id={`${id}-actor`}
              value={value.actor}
              onChange={(e) => set('actor', e.target.value)}
              placeholder="dana.okoye…"
              autoComplete="off"
              spellCheck={false}
              className={cn(INPUT, 'h-9 w-full')}
            />
          </Field>

          <Field
            label="Action starts with"
            htmlFor={`${id}-action`}
            tip="A prefix, so an action family answers as one: query. · guardrail. · approval. · documents. · jobs. · tool:"
          >
            <input
              id={`${id}-action`}
              value={value.actionPrefix}
              onChange={(e) => set('actionPrefix', e.target.value)}
              placeholder="guardrail. · tool:…"
              autoComplete="off"
              spellCheck={false}
              className={cn(INPUT, 'h-9 w-full')}
            />
          </Field>

          <Field
            label="Model"
            htmlFor={`${id}-model`}
            tip="The exact deployment id recorded on the row — what actually served the call, not the family it belongs to."
          >
            <input
              id={`${id}-model`}
              value={value.model}
              onChange={(e) => set('model', e.target.value)}
              placeholder="gpt-4o-mini…"
              autoComplete="off"
              spellCheck={false}
              className={cn(INPUT, 'h-9 w-full')}
            />
          </Field>

          {/* Only a platform admin is handed tenants to choose between; for everybody else
              the server pins the scope and there is nothing to select. */}
          {tenants.length > 0 ? (
            <Field
              label="Tenant"
              htmlFor={`${id}-tenant`}
              tip="A selector, never a scope. The server grants it only to a platform admin; a tenant-bound caller naming another tenant is refused whether that tenant exists or not."
            >
              <select
                id={`${id}-tenant`}
                value={value.tenantId === null ? '' : String(value.tenantId)}
                onChange={(e) =>
                  set('tenantId', e.target.value === '' ? null : Number(e.target.value))
                }
                className={cn(INPUT, 'h-9 w-full')}
              >
                <option value="">Every tenant</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}

          <Field
            label="From"
            htmlFor={`${id}-since`}
            tip="Wall-clock in your own zone. The API compares against UTC, and the conversion happens here so “since 09:00” means 09:00 where you are standing."
          >
            <input
              id={`${id}-since`}
              type="datetime-local"
              value={value.since}
              onChange={(e) => set('since', e.target.value)}
              className={cn(INPUT, 'h-9 w-full')}
            />
          </Field>

          <Field label="To" htmlFor={`${id}-until`}>
            <input
              id={`${id}-until`}
              type="datetime-local"
              value={value.until}
              onChange={(e) => set('until', e.target.value)}
              className={cn(INPUT, 'h-9 w-full')}
            />
          </Field>
        </div>
      ) : null}

      {/* What is actually on, said where it cannot scroll away. */}
      {filtered ? (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-2.5">
          <span className="eyebrow mb-0">filtering by</span>
          {chips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              onClick={() => patch(chip.clear)}
              className={cn(
                'inline-flex h-6 touch-manipulation items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 font-mono text-[0.6875rem] text-blue-700 transition-colors duration-[--dur-fast] hover:bg-blue-100',
                FOCUS,
              )}
            >
              {chip.label}
              <X aria-hidden className="size-3" />
              <span className="sr-only">— remove this filter</span>
            </button>
          ))}
          <button
            type="button"
            onClick={() => onChange({ ...EMPTY_AUDIT_QUERY, limit: value.limit })}
            className={cn(
              'ml-auto inline-flex h-6 touch-manipulation items-center gap-1.5 rounded-full px-2 font-mono text-[0.6875rem] text-muted-foreground transition-colors hover:text-foreground',
              FOCUS,
            )}
          >
            <RotateCcw aria-hidden className="size-3" /> Clear all
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** Shared control styling — one place, so every field in the strip lines up. */
const INPUT =
  'rounded-lg border border-border bg-card px-2 text-[0.75rem] text-foreground placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'

/** The one focus treatment on this strip: the ring token, at 2px, always visible. */
const FOCUS = 'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'

/** A labelled control. The label is real (`htmlFor`), not a placeholder standing in. */
function Field({
  label,
  htmlFor,
  tip,
  className,
  children,
}: {
  label: string
  htmlFor: string
  tip?: string
  className?: string
  children: ReactNode
}): ReactElement {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="eyebrow mb-0 inline-flex items-center gap-1">
        {label}
        {tip ? <InfoTip label={`About the ${label} filter`}>{tip}</InfoTip> : null}
      </label>
      {children}
    </div>
  )
}
