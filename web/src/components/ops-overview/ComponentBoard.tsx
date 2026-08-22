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

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

import {
  ago,
  categoryLabel,
  groupByCategory,
  verdictSplit,
  type ComponentStatus,
  type ReadyComponent,
  type ReadyzResponse,
  asStatus,
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
interface Verdict {
  icon: LucideIcon
  word: string
  tone: Signal
  /** The solid step, for a strip segment — a mark, never text. */
  fill: string
  /** The tile: a wash, a hairline and a 3px spine, all one hue. */
  tile: string
  /** The glyph's ink — the 700 step that is legible on the wash (DESIGN.md §2). */
  ink: string
}

const VERDICT: Record<ComponentStatus, Verdict> = {
  up: {
    icon: CircleCheck,
    word: 'Up',
    tone: 'ok',
    fill: 'bg-ok',
    tile: 'border-ok/35 border-l-ok bg-ok/8',
    ink: 'text-ok-ink',
  },
  down: {
    icon: OctagonX,
    word: 'Down',
    tone: 'block',
    fill: 'bg-block',
    tile: 'border-block/45 border-l-block bg-block/10',
    ink: 'text-block-ink',
  },
  degraded: {
    icon: TriangleAlert,
    word: 'Degraded',
    tone: 'risk',
    fill: 'bg-risk',
    tile: 'border-risk/45 border-l-risk bg-risk/10',
    ink: 'text-risk-ink',
  },
  unknown: {
    icon: CircleHelp,
    word: 'Unknown',
    tone: 'neutral',
    fill: 'bg-muted-foreground/45',
    tile: 'border-border border-l-muted-foreground/60 bg-surface-2/60',
    ink: 'text-muted-foreground',
  },
  not_applicable: {
    icon: CircleSlash,
    word: 'Not applicable',
    tone: 'neutral',
    fill: 'bg-muted-foreground/25',
    tile: 'border-border border-l-border bg-surface-2/40',
    ink: 'text-muted-foreground',
  },
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
                    <StatusChip status={asStatus(c.status)} />
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

/**
 * The health strip — the whole platform's up-versus-down as one bar.
 *
 * This is the mark the board leads with, and it exists because the fact an
 * operator wants in the first half-second — *is anything broken* — was previously
 * only recoverable by reading eight paragraphs and holding the tally in your head.
 * Every width is a real count over the real denominator ({@link verdictSplit});
 * nothing is padded and no verdict the platform is not currently in gets a
 * segment, so the legend below only ever names states that actually occurred.
 *
 * The strip is `role="img"` with the tally spelled out, and each segment carries
 * its word in the legend — the hue is the fast read, never the only one
 * (DESIGN.md §2).
 */
function HealthStrip({ components }: { components: ReadyComponent[] }): ReactElement | null {
  const slices = verdictSplit(components)
  if (slices.length === 0) return null
  const up = slices.find((s) => s.status === 'up')?.count ?? 0
  const spoken = slices.map((s) => `${s.count} ${VERDICT[s.status].word.toLowerCase()}`).join(', ')
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-6 gap-y-3">
      <p className="min-w-0 shrink-0">
        <span className="eyebrow block">components up</span>
        <Figure size="stat" className="text-foreground" label={`${up} of ${components.length} components up`}>
          {up}/{components.length}
        </Figure>
      </p>
      <div className="min-w-0 flex-1 basis-64 space-y-2">
        <div
          className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-surface-2"
          role="img"
          aria-label={`${components.length} components: ${spoken}`}
        >
          {slices.map((slice) => (
            <span
              key={slice.status}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', VERDICT[slice.status].fill)}
              style={{ width: `${slice.share * 100}%` }}
            />
          ))}
        </div>
        <ul className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5">
          {slices.map((slice) => {
            const verdict = VERDICT[slice.status]
            const Icon = verdict.icon
            return (
              <li key={slice.status} className="flex items-center gap-1.5">
                <Icon className={cn('size-3.5 shrink-0', verdict.ink)} aria-hidden />
                <span className="text-[0.8125rem] text-foreground">{verdict.word}</span>
                <Figure className="text-muted-foreground">{slice.count}</Figure>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

/**
 * One component as a tile: the glyph and hue you read at a glance, the name, and
 * everything else one keystroke away.
 *
 * **Where the evidence went.** Each verdict used to carry its probe command as a
 * line of monospace under it — eight of them, and the honesty that made Aegis
 * worth showing was what made the panel unreadable. It is in the tile's
 * {@link InfoTip} now, along with the component key, its category and whatever
 * `detail` the server sent, reachable by hover *and* by keyboard focus. Nothing
 * was deleted; it stopped being always-on.
 *
 * The status word stays on the tile face rather than moving into the tip, because
 * DESIGN.md §2 does not let a hue carry a state on its own — the tint is the fast
 * read and the glyph plus the word are what make it a fact.
 */
function ComponentTile({ component, now }: { component: ReadyComponent; now: number }): ReactElement {
  const status = asStatus(component.status)
  const verdict = VERDICT[status]
  const Icon = verdict.icon
  return (
    <li
      className={cn(
        'flex min-w-0 flex-col gap-1.5 rounded-md border border-l-[3px] p-3',
        verdict.tile,
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <Icon className={cn('size-4 shrink-0', verdict.ink)} aria-hidden />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground" title={component.name}>
          {component.name}
        </span>
        <InfoTip label={`Evidence for ${component.name}`} className="shrink-0">
          <span className="block space-y-1.5">
            <span className="tabular block font-mono text-foreground">
              {component.key} · {categoryLabel(component.category)}
            </span>
            {component.detail == null ? null : <span className="block">{component.detail}</span>}
            <span className="block">
              <span className="text-foreground">Evidence:</span>{' '}
              {component.evidence ?? 'no evidence reported by the probe'}
            </span>
          </span>
        </InfoTip>
      </div>
      <div className="flex min-w-0 items-center justify-between gap-2">
        <span className="eyebrow truncate">
          <span className={verdict.ink}>{verdict.word}</span>
          <span className="text-muted-foreground"> · {component.required ? 'required' : 'optional'}</span>
        </span>
        <Figure className="shrink-0 text-muted-foreground">{ago(component.measured_at, now)}</Figure>
      </div>
    </li>
  )
}

/**
 * The component health board — every dependency `/readyz` probes, as one strip and
 * a grid of tiles.
 *
 * **What this replaced.** Eight stacked prose rows, each a verdict chip followed by
 * a wrapped line of probe evidence, inside four category sections: roughly 730px of
 * monospace on a 1440px screen to say "everything is up". The panel's own header
 * already knew the answer — *8 components · 8 up* — and that fact was the one thing
 * not drawn.
 *
 * Now the strip answers it in one mark, and the tiles answer *which* in eight.
 * The tiles are one flat grid rather than four category sections: with four stores,
 * one substrate, two model-plane components and one isolation check, per-category
 * rows leave nine empty cells and three extra headings. The **order** is still
 * category-then-severity ({@link groupByCategory}), so a component sits where an
 * operator learned to look for it; the category itself is on the tile's tip.
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
  const ordered = groupByCategory(data.components).flatMap((group) => group.rows)

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader
        eyebrow="aegis · GET /readyz"
        title="Component health"
        actions={
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <DeepLink href={`/app/${portal}/security`} icon={ShieldCheck} label="Security posture" />
            <DeepLink href={`/app/${portal}/redteam`} icon={Swords} label="Red team" />
          </div>
        }
      />
      <CardBody className="flex min-w-0 flex-col gap-4 pt-0">
        <HealthStrip components={data.components} />
        <ul className="grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {ordered.map((component) => (
            <ComponentTile key={component.key} component={component} now={now} />
          ))}
        </ul>
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
