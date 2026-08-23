'use client'

import {
  ChevronRight,
  CircleCheck,
  CircleDashed,
  CircleSlash,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { cn } from '@/lib/utils'
import type { EgressChannel, Locality, ResidencyReport } from '@/lib/api/platform'

/**
 * The four localities, best-first, each with the icon, word and hue it always ships with.
 *
 * No new hue is introduced (DESIGN.md §2): `local` takes `--ok`, `external` takes
 * `--risk`, `unknown` takes `--block` and `disabled` is neutral. `external` is amber
 * rather than red on purpose — the model gateway is *meant* to be off-host, so the mark
 * says "look at this", not "this is broken". `unknown` is the red one, because a
 * destination the surface could not parse is the one it must not reassure anybody about.
 */
const LOCALITY_META: Record<
  Locality,
  { label: string; icon: LucideIcon; ink: string; fill: string; cell: string }
> = {
  local: {
    label: 'on this host',
    icon: CircleCheck,
    ink: 'text-ok-ink',
    fill: 'bg-ok',
    cell: 'border-ok bg-ok/15 text-ok-ink',
  },
  external: {
    label: 'leaves',
    icon: TriangleAlert,
    ink: 'text-risk-ink',
    fill: 'bg-risk',
    cell: 'border-risk bg-risk/15 text-risk-ink',
  },
  unknown: {
    label: 'unreadable',
    icon: CircleSlash,
    ink: 'text-block-ink',
    fill: 'bg-block',
    cell: 'border-block bg-block/15 text-block-ink',
  },
  disabled: {
    label: 'not configured',
    icon: CircleDashed,
    ink: 'text-muted-foreground',
    fill: 'bg-muted-foreground/40',
    cell: 'border-border bg-surface-2 text-muted-foreground',
  },
}

const LOCALITY_ORDER: Locality[] = ['local', 'external', 'unknown', 'disabled']

/** Coerce a widened locality string to a known band, defaulting to the honest one. */
function localityOf(value: string): Locality {
  return value === 'local' || value === 'external' || value === 'disabled' ? value : 'unknown'
}

/** The three groups, in the order a reviewer reads them: at rest, then out, then self. */
const ROLE_GROUPS: { role: string; eyebrow: string }[] = [
  { role: 'store', eyebrow: 'data at rest' },
  { role: 'process', eyebrow: 'in transit — what leaves' },
  { role: 'self', eyebrow: "aegis's own address" },
]

/** A locality pill — icon, word and hue together, never hue alone (DESIGN.md §2). */
function LocalityPill({ locality }: { locality: string }): ReactElement {
  const meta = LOCALITY_META[localityOf(locality)]
  const Icon = meta.icon
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.75rem] font-medium',
        meta.cell,
      )}
    >
      <Icon className="size-3 shrink-0" aria-hidden />
      {meta.label}
    </span>
  )
}

/**
 * The mark this panel opens with: how many destinations, and how many of them leave.
 *
 * DESIGN.md §4 — a state is drawn before it is described. Fifteen destinations each
 * carrying a sentence would answer "where does Redis point?" perfectly and "does
 * anything leave this box?" not at all. Zero is not drawn: a locality with no channels
 * emits no segment.
 */
function LocalityStrip({ counts }: { counts: Record<Locality, number> }): ReactElement {
  const total = LOCALITY_ORDER.reduce((sum, band) => sum + counts[band], 0) || 1
  const present = LOCALITY_ORDER.filter((band) => counts[band] > 0)
  return (
    <div
      role="img"
      aria-label={present
        .map((band) => `${counts[band]} ${LOCALITY_META[band].label}`)
        .join(', ')}
      className="flex h-2.5 w-full min-w-0 overflow-hidden rounded-full bg-surface-2"
    >
      {present.map((band) => (
        <span
          key={band}
          className={cn('block h-full', LOCALITY_META[band].fill)}
          style={{ width: `${(counts[band] / total) * 100}%` }}
        />
      ))}
    </div>
  )
}

/**
 * One destination: the address and its verdict on the face, what travels there one layer
 * down.
 *
 * The closed row is the fact a reviewer scans for — where does this point, and does it
 * leave. Opening it gives the sentence saying what actually travels, the setting that
 * decides it, and the file where the dial happens. Nothing is dropped; it moves.
 */
function ChannelRow({ channel }: { channel: EgressChannel }): ReactElement {
  const [open, setOpen] = useState(false)
  const panelId = `residency-${channel.id}`
  return (
    <li className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
        className="flex w-full min-w-0 items-center gap-3 px-1 py-2.5 text-left transition-colors duration-[--dur-fast] hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform duration-[--dur-fast]',
            open && 'rotate-90',
          )}
        />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5 lg:flex-row lg:items-center lg:gap-3">
          {/*
            Wraps below `lg`, truncates above it. Inline at 390px a fixed name column
            left about 150 pixels and every row read "PostgreSQL — the sy…", "Model
            gateway — the sin…" — a list of ellipses, which is the one place a clipped
            label lies about the destination it names. Two lines is the cheaper cost.
          */}
          <span className="min-w-0 text-sm text-foreground lg:flex-1 lg:truncate">
            {channel.name}
          </span>
          {channel.destination ? (
            <Figure
              truncate
              className="min-w-0 text-muted-foreground lg:w-64 lg:shrink-0 lg:justify-end"
            >
              <span translate="no">{channel.destination}</span>
            </Figure>
          ) : null}
        </span>
        <LocalityPill locality={channel.locality} />
      </button>

      {open ? (
        <div id={panelId} className="min-w-0 space-y-2 px-1 pb-4 pl-8">
          <p className="max-w-prose text-pretty text-sm leading-relaxed text-foreground">
            {channel.carries}
          </p>
          <dl className="grid min-w-0 gap-x-6 gap-y-1 sm:grid-cols-2">
            {/*
              The address is repeated here in full because the closed row truncates it,
              and at 390px three Postgres roles all clip to the same prefix. Evidence
              relocated off the face has to stay reachable (DESIGN.md §4) — clipping it
              in both places would be losing it, not hiding it.
            */}
            {channel.destination ? (
              <div className="flex min-w-0 flex-col gap-0.5 sm:col-span-2">
                <dt className="eyebrow">address</dt>
                <dd>
                  <Figure className="min-w-0 break-all text-foreground">
                    <span translate="no">{channel.destination}</span>
                  </Figure>
                </dd>
              </div>
            ) : null}
            <div className="flex min-w-0 flex-col gap-0.5">
              <dt className="eyebrow">decided by</dt>
              <dd>
                <Figure className="min-w-0 break-all text-foreground">
                  <span translate="no">{channel.setting}</span>
                </Figure>
              </dd>
            </div>
            <div className="flex min-w-0 flex-col gap-0.5">
              <dt className="eyebrow">where it happens</dt>
              <dd>
                <Figure className="min-w-0 break-all text-foreground">
                  <span translate="no">{channel.code_ref}</span>
                </Figure>
              </dd>
            </div>
          </dl>
        </div>
      ) : null}
    </li>
  )
}

/**
 * Data residency — the derived answer to the one question DPDP s.16 and CERT-In
 * Direction (iv) both turn on.
 *
 * It is on this screen rather than in a paragraph because a written residency claim is
 * true on the day it is typed and false the first time somebody edits an environment
 * variable. Every row here is parsed from live configuration on each read, so
 * re-pointing a store at an outside host changes what a reviewer sees.
 */
export function ResidencyPanel({ residency }: { residency: ResidencyReport }): ReactElement {
  const counts = LOCALITY_ORDER.reduce(
    (acc, band) => {
      acc[band] = residency.channels.filter((c) => localityOf(c.locality) === band).length
      return acc
    },
    { local: 0, external: 0, unknown: 0, disabled: 0 } as Record<Locality, number>,
  )
  const stores = residency.stores_local + residency.stores_external

  return (
    <Card className="min-w-0">
      <CardHeader
        eyebrow="aegis.residency · derived on every read"
        title="Where this deployment's data goes"
        actions={
          <InfoTip label="What this inventory can and cannot establish">{residency.note}</InfoTip>
        }
      />
      <CardBody className="flex min-w-0 flex-col gap-3 pt-0">
        {/*
          The headline fact, drawn before anything is described: how many stores hold
          tenant data on this host, and how many destinations leave it at all.
        */}
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-6 gap-y-1">
          <span className="flex min-w-0 items-baseline gap-2">
            <Figure size="stat" className="text-foreground">
              {residency.stores_local}/{stores}
            </Figure>
            <span className="text-[0.8125rem] text-muted-foreground">stores on this host</span>
          </span>
          <span className="flex min-w-0 items-baseline gap-2">
            <Figure size="stat" className="text-foreground">
              {residency.external}
            </Figure>
            <span className="text-[0.8125rem] text-muted-foreground">
              of {residency.channels.length} destinations leave it
            </span>
          </span>
        </div>

        <LocalityStrip counts={counts} />
        <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-3">
          {LOCALITY_ORDER.filter((band) => counts[band] > 0).map((band) => {
            const meta = LOCALITY_META[band]
            const Icon = meta.icon
            return (
              <li key={band} className="flex min-w-0 items-center gap-2">
                <Icon className={cn('size-3.5 shrink-0', meta.ink)} aria-hidden />
                <Figure size="stat" className="text-foreground">
                  {counts[band]}
                </Figure>
                <span className="text-[0.8125rem] text-muted-foreground">{meta.label}</span>
              </li>
            )
          })}
        </ul>

        {ROLE_GROUPS.map(({ role, eyebrow }) => {
          const rows = residency.channels.filter((channel) => channel.role === role)
          if (rows.length === 0) return null
          return (
            <div key={role} className="min-w-0">
              <p className="eyebrow border-t border-border pt-3">{eyebrow}</p>
              <ul className="min-w-0">
                {rows.map((channel) => (
                  <ChannelRow key={channel.id} channel={channel} />
                ))}
              </ul>
            </div>
          )
        })}

        <Receipt
          origin="GET /compliance · residency"
          detail="each address is parsed from the setting beside it on every read, never written down"
        />
      </CardBody>
    </Card>
  )
}
