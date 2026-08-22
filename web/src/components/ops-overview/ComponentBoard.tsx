'use client'

import {
  CircleCheck,
  CircleHelp,
  CircleSlash,
  OctagonX,
  ShieldCheck,
  Swords,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import Link from 'next/link'
import type { ReactElement } from 'react'

import { Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

import {
  ago,
  categoryLabel,
  groupByCategory,
  tally,
  type ComponentStatus,
  type ReadyComponent,
  type ReadyzResponse,
} from './readiness'

/**
 * The presentation of one verdict — **an icon and a word, never a hue alone**
 * (DESIGN.md §2). `risk`, `block` and `ok` fail CVD separation against each other by
 * design; the glyph and the label are what actually distinguish them.
 *
 * `unknown` wears the neutral tone, not the amber one. A probe that timed out has
 * established nothing, and an operator who learns that amber sometimes means "no
 * answer" stops trusting amber when it means "degraded".
 */
const VERDICT: Record<ComponentStatus, { icon: LucideIcon; word: string; tone: Signal }> = {
  up: { icon: CircleCheck, word: 'Up', tone: 'ok' },
  down: { icon: OctagonX, word: 'Down', tone: 'block' },
  degraded: { icon: TriangleAlert, word: 'Degraded', tone: 'risk' },
  unknown: { icon: CircleHelp, word: 'Unknown', tone: 'neutral' },
  not_applicable: { icon: CircleSlash, word: 'Not applicable', tone: 'neutral' },
}

/** The status chip: glyph, word, tone — in that order, always all three. */
export function StatusChip({ status }: { status: ComponentStatus }): ReactElement {
  const verdict = VERDICT[status] ?? VERDICT.unknown
  const Icon = verdict.icon
  return (
    <Badge tone={verdict.tone} className="shrink-0 gap-1.5">
      <Icon className="size-3.5" aria-hidden />
      {verdict.word}
    </Badge>
  )
}

/**
 * The verdict banner — the server's own readiness decision, at the top of the screen.
 *
 * It renders `status` and `failing` as they arrived rather than re-deriving them:
 * `/readyz` is what the load balancer holds this deployment to, so a browser that
 * computed its own opinion could disagree with the thing actually routing traffic.
 *
 * A failing **required** component takes the block tone, the octagon glyph, the word
 * "Not ready" and the server's own remediation sentence. Everything else on the page
 * is quiet so that this is not.
 */
export function ReadinessVerdict({ data }: { data: ReadyzResponse }): ReactElement {
  const failing = data.components.filter((c) => data.failing.includes(c.key))
  const notReady = data.status !== 'ready' || failing.length > 0
  const optionalDown = data.components.filter((c) => c.status === 'down' && !data.failing.includes(c.key))
  const unknown = data.components.filter((c) => c.status === 'unknown')

  if (notReady) {
    return (
      <Card className="border-block bg-block/10">
        <CardBody className="flex min-w-0 items-start gap-3">
          <OctagonX className="mt-0.5 size-6 shrink-0 text-block-ink" aria-hidden />
          <div className="min-w-0 space-y-2">
            <p className="text-pretty text-base leading-6 font-semibold text-foreground">
              Not ready — {failing.length === 1 ? 'a required component is' : `${failing.length} required components are`} down,
              and <span className="font-mono">/readyz</span> is refusing traffic.
            </p>
            <ul className="space-y-2">
              {failing.map((c) => (
                <li key={c.key} className="min-w-0 space-y-1">
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium text-foreground">
                    <StatusChip status={c.status} />
                    <span className="min-w-0 break-words">{c.name}</span>
                    <span className="tabular font-mono text-xs text-muted-foreground">{c.key}</span>
                  </p>
                  {c.detail == null ? null : (
                    <p className="min-w-0 text-pretty text-xs leading-relaxed break-words text-muted-foreground">
                      {c.detail}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </CardBody>
      </Card>
    )
  }

  const caveat =
    optionalDown.length > 0
      ? `${optionalDown.length} optional component${optionalDown.length === 1 ? '' : 's'} down — traffic is still served.`
      : unknown.length > 0
        ? `${unknown.length} probe${unknown.length === 1 ? '' : 's'} did not answer — unknown is not down.`
        : `All ${data.components.length} components answered.`

  return (
    <Card className="border-ok bg-ok/10">
      <CardBody className="flex min-w-0 items-center gap-3">
        <CircleCheck className="size-6 shrink-0 text-ok-ink" aria-hidden />
        <p className="min-w-0 text-pretty text-base leading-6 font-semibold text-foreground">
          Ready — <span className="font-normal text-muted-foreground">{caveat}</span>
        </p>
      </CardBody>
    </Card>
  )
}

/** One component: verdict, identity, how fresh the reading is, and its evidence. */
function ComponentRow({
  component,
  now,
  /** True when the banner above already carries this component's remediation text. */
  detailShownAbove,
}: {
  component: ReadyComponent
  now: number
  detailShownAbove: boolean
}): ReactElement {
  const failingRequired = component.status === 'down' && component.required
  return (
    <li
      className={cn(
        'min-w-0 border-l-2 py-3 pl-3 first:pt-0 last:pb-0',
        failingRequired ? 'border-l-block bg-block/5' : 'border-l-transparent',
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <StatusChip status={component.status} />
        <span className="min-w-0 text-sm font-medium break-words text-foreground">{component.name}</span>
        <span className="tabular font-mono text-xs text-muted-foreground">{component.key}</span>
        <span
          className={cn(
            'tabular ml-auto shrink-0 font-mono text-xs',
            failingRequired ? 'font-semibold text-block-ink' : 'text-muted-foreground',
          )}
        >
          {component.required ? 'required' : 'optional'}
        </span>
        <span className="tabular shrink-0 font-mono text-xs text-muted-foreground">
          {ago(component.measured_at, now)}
        </span>
      </div>
      {component.detail == null || detailShownAbove ? null : (
        <p className="mt-1.5 min-w-0 text-pretty text-xs leading-relaxed break-words text-muted-foreground">
          {component.detail}
        </p>
      )}
      <Receipt label="Evidence" origin={component.evidence} variant="inline" className="mt-1.5" />
    </li>
  )
}

/**
 * The component health board — every dependency `/readyz` probes, grouped by the
 * category the server assigned it.
 *
 * One panel rather than four, because the categories are uneven (four stores, one
 * substrate) and a grid of cards sized by their tallest member is mostly whitespace.
 * Inside a group the worst verdict sorts first; the groups themselves keep a fixed
 * order so a component stays where an operator learned to look for it.
 */
export function ComponentBoard({
  data,
  now,
  portal,
  className,
}: {
  data: ReadyzResponse
  now: number
  portal: string
  className?: string
}): ReactElement {
  const groups = groupByCategory(data.components)
  const counts = tally(data.components)
  const summary = [
    `${counts.up} up`,
    counts.degraded > 0 ? `${counts.degraded} degraded` : null,
    counts.down > 0 ? `${counts.down} down` : null,
    counts.unknown > 0 ? `${counts.unknown} unknown` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader
        eyebrow={`${data.components.length} components · ${summary}`}
        title="Component health"
        actions={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <DeepLink href={`/app/${portal}/security`} icon={ShieldCheck} label="Security posture" />
            <DeepLink href={`/app/${portal}/redteam`} icon={Swords} label="Red team" />
          </div>
        }
      />
      <CardBody className="flex min-w-0 flex-col gap-5 pt-0">
        {groups.map(({ category, rows }) => (
          <section key={category} className="min-w-0">
            <h3 className="eyebrow mb-1">{categoryLabel(category)}</h3>
            <ul className="min-w-0 divide-y divide-border/70">
              {rows.map((component) => (
                <ComponentRow
                  key={component.key}
                  component={component}
                  now={now}
                  detailShownAbove={data.failing.includes(component.key)}
                />
              ))}
            </ul>
          </section>
        ))}
      </CardBody>
    </Card>
  )
}

/** A link through to the screen that owns the detail this panel only summarises. */
export function DeepLink({
  href,
  icon: Icon,
  label,
}: {
  href: string
  icon: LucideIcon
  label: string
}): ReactElement {
  return (
    <Link
      href={href}
      className="inline-flex items-center gap-1.5 rounded-md text-xs font-medium text-blue-700 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
    >
      <Icon className="size-3.5" aria-hidden />
      {label}
    </Link>
  )
}
