'use client'

import { RotateCcw, Search } from 'lucide-react'
import { useId, type ReactElement, type ReactNode } from 'react'

import type { Tenant } from '@/lib/api/types'
import { cn } from '@/lib/utils'

import { EMPTY_AUDIT_QUERY, isFiltered, type AuditQuery } from './query'

interface AuditFilterBarProps {
  value: AuditQuery
  onChange: (next: AuditQuery) => void
  /** Tenants the caller may select between — empty for everyone but a platform admin. */
  tenants?: Tenant[]
  /** Whether a fetch is in flight, so the strip can say the numbers are moving. */
  busy?: boolean
}

/**
 * The audit filter strip — actor, action family, model, outcome, free text, time range,
 * and (platform admin only) the tenant selector.
 *
 * Every control here changes a **server** query. That is the point of §7.11 and the
 * reason the strip is a form rather than a set of chips over a fetched array: the
 * question an operator asks ("what did this actor do last week") is about the trail, and
 * only the database can answer it.
 */
export function AuditFilterBar({
  value,
  onChange,
  tenants = [],
  busy = false,
}: AuditFilterBarProps): ReactElement {
  const id = useId()
  const set = <K extends keyof AuditQuery>(key: K, next: AuditQuery[K]): void => {
    onChange({ ...value, [key]: next })
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface-2/40 p-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Search" htmlFor={`${id}-text`} className="min-w-[13rem] flex-1">
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground/60"
            />
            <input
              id={`${id}-text`}
              value={value.text}
              onChange={(e) => set('text', e.target.value)}
              placeholder="Action, actor, model, trace…"
              className={cn(INPUT, 'w-full pl-7')}
            />
          </div>
        </Field>

        <Field label="Actor" htmlFor={`${id}-actor`}>
          <input
            id={`${id}-actor`}
            value={value.actor}
            onChange={(e) => set('actor', e.target.value)}
            placeholder="exact username"
            className={cn(INPUT, 'w-40')}
          />
        </Field>

        <Field label="Action starts with" htmlFor={`${id}-action`}>
          <input
            id={`${id}-action`}
            value={value.actionPrefix}
            onChange={(e) => set('actionPrefix', e.target.value)}
            placeholder="ops. · tool:"
            className={cn(INPUT, 'w-36')}
          />
        </Field>

        <Field label="Model" htmlFor={`${id}-model`}>
          <input
            id={`${id}-model`}
            value={value.model}
            onChange={(e) => set('model', e.target.value)}
            placeholder="deployment id"
            className={cn(INPUT, 'w-40')}
          />
        </Field>

        <Field label="Outcome" htmlFor={`${id}-outcome`}>
          <select
            id={`${id}-outcome`}
            value={value.outcome ?? ''}
            onChange={(e) =>
              set('outcome', e.target.value === '' ? null : (e.target.value as 'blocked' | 'completed'))
            }
            className={cn(INPUT, 'w-32')}
          >
            <option value="">Any</option>
            <option value="completed">Completed</option>
            <option value="blocked">Blocked</option>
          </select>
        </Field>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="From" htmlFor={`${id}-since`}>
          <input
            id={`${id}-since`}
            type="datetime-local"
            value={value.since}
            onChange={(e) => set('since', e.target.value)}
            className={cn(INPUT, 'w-52')}
          />
        </Field>

        <Field label="To" htmlFor={`${id}-until`}>
          <input
            id={`${id}-until`}
            type="datetime-local"
            value={value.until}
            onChange={(e) => set('until', e.target.value)}
            className={cn(INPUT, 'w-52')}
          />
        </Field>

        <Field label="Rows" htmlFor={`${id}-limit`}>
          <select
            id={`${id}-limit`}
            value={String(value.limit)}
            onChange={(e) => set('limit', Number(e.target.value))}
            className={cn(INPUT, 'w-24')}
          >
            {[50, 100, 200].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Field>

        {/* Only a platform admin is handed tenants to choose between; for everybody else
            the server pins the scope and there is nothing to select. */}
        {tenants.length > 0 && (
          <Field label="Tenant" htmlFor={`${id}-tenant`}>
            <select
              id={`${id}-tenant`}
              value={value.tenantId === null ? '' : String(value.tenantId)}
              onChange={(e) => set('tenantId', e.target.value === '' ? null : Number(e.target.value))}
              className={cn(INPUT, 'w-48')}
            >
              <option value="">Every tenant</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        <div className="ml-auto flex items-center gap-2">
          {busy && (
            <span aria-live="polite" className="font-mono text-[0.68rem] text-muted-foreground">
              Loading…
            </span>
          )}
          <button
            type="button"
            onClick={() => onChange({ ...EMPTY_AUDIT_QUERY, limit: value.limit })}
            disabled={!isFiltered(value)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 font-mono text-[0.7rem] text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-45"
          >
            <RotateCcw aria-hidden className="size-3.5" /> Clear filters
          </button>
        </div>
      </div>
    </div>
  )
}

/** Shared control styling — one place, so every field in the strip lines up. */
const INPUT =
  'h-8 rounded-md border border-border bg-card px-2 text-[0.75rem] text-foreground placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'

/** A labelled control. The label is real (`htmlFor`), not a placeholder standing in. */
function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string
  htmlFor: string
  className?: string
  children: ReactNode
}): ReactElement {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="eyebrow">
        {label}
      </label>
      {children}
    </div>
  )
}
