'use client'

import { type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import type {
  EmissionChannel,
  PipelineDeclaration,
  PipelineStage,
  PipelinesResponse,
} from '@/lib/api/pipeline'

/**
 * The declared pipelines — three flows, their stages, and what each stage emits.
 *
 * **This panel renders a contract, not a diagram.** Everything below comes from
 * `GET /pipelines`, which serves `aegis.pipelines.spec` after verifying it against the
 * code it describes: the ingest stage tuple a resume walks, the agent graph's own node
 * labels, and the retrieval observability model's fields. Nothing here is a second copy
 * of a list that lives in Python — which is precisely how the orchestration map came to
 * draw nine nodes for a seventeen-node graph, and how `CacheView` carried a hardcoded
 * `SPECS` array until §7.10b deleted it.
 *
 * The channel legend is served too, for the same reason: a sentence explaining what
 * `stream` means, restated here, would be one more thing that can go stale.
 */

/** Channel → how the pill reads. `run_event` is the only one that survives the request. */
const CHANNEL_TONE: Record<EmissionChannel, BadgeTone> = {
  run_event: 'ok',
  stream: 'agent',
  result: 'graph',
}

/** One stage row: what runs, who owns it, and everything it puts on a wire or a row. */
function StageRow({ stage, index }: { stage: PipelineStage; index: number }): ReactElement {
  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap text-muted-foreground">
        <Figure>{index + 1}</Figure>
      </TD>
      <TD className="whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          <div className="flex min-w-0 flex-col gap-0.5">
            <Figure className="font-medium text-foreground">{stage.name}</Figure>
            <span className="text-[0.7rem] text-muted-foreground">{stage.label}</span>
            {stage.optional ? (
              <span className="pt-0.5">
                <Badge tone="neutral">conditional</Badge>
              </span>
            ) : null}
          </div>
          {/*
            The stage sentence, relocated. Thirty-one of them rendered at once is the
            wall of text DESIGN.md §9 names outright; the sentence is not deleted, it
            is one hover from the name it describes (§4).
          */}
          <InfoTip label={`What ${stage.name} does`}>{stage.summary}</InfoTip>
        </div>
      </TD>
      <TD>
        <Figure className="leading-relaxed text-muted-foreground">{stage.owner}</Figure>
      </TD>
      <TD>
        {stage.emits.length === 0 ? (
          <span className="text-[0.75rem] text-muted-foreground">nothing observable</span>
        ) : (
          <ul className="flex max-w-lg flex-col gap-1.5">
            {stage.emits.map((emission) => (
              <li key={`${emission.channel}:${emission.name}`} className="flex flex-wrap items-center gap-1.5">
                <Badge tone={CHANNEL_TONE[emission.channel] ?? 'neutral'}>
                  {emission.channel}
                </Badge>
                <Figure className="text-foreground">{emission.name}</Figure>
                <InfoTip label={`What ${emission.name} carries`}>{emission.detail}</InfoTip>
              </li>
            ))}
          </ul>
        )}
      </TD>
    </TR>
  )
}

/** One pipeline: its entry point, its durable record, its stages, and its blind spots. */
function PipelineCard({ pipeline }: { pipeline: PipelineDeclaration }): ReactElement {
  return (
    <DataPanel
      eyebrow={`${pipeline.stages.length} stages · ${pipeline.entrypoint}`}
      title={pipeline.title}
      maxHeight={520}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {pipeline.durable_record ? (
            <Badge tone="ok">persisted to {pipeline.durable_record}</Badge>
          ) : (
            <Badge tone="neutral">nothing persisted</Badge>
          )}
          <InfoTip label={`What the ${pipeline.title} pipeline is`}>{pipeline.summary}</InfoTip>
        </div>
      }
      footer={
        pipeline.limits.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="eyebrow">does not record</span>
            {pipeline.limits.map((limit) => (
              <span
                key={limit}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-2 py-1 text-[0.72rem] text-muted-foreground"
              >
                {/* The limit, kept whole — the first clause reads, the rest is a hover away. */}
                {limit.split(/[.;]/)[0]}
                <InfoTip label="The whole limit, as declared">{limit}</InfoTip>
              </span>
            ))}
          </div>
        ) : undefined
      }
    >
      <Table className="min-w-[640px]">
        <THead>
          <TH className="w-8">#</TH>
          <TH>Stage</TH>
          <TH>Who owns it</TH>
          <TH>What it emits</TH>
        </THead>
        <TBody>
          {pipeline.stages.map((stage, index) => (
            <StageRow key={stage.name} stage={stage} index={index} />
          ))}
        </TBody>
      </Table>
    </DataPanel>
  )
}

/**
 * Every declared pipeline, with the channel legend that makes the emissions readable.
 *
 * Rendered beneath the measured figures on purpose: the declaration is what the figures
 * above are read *against*, so a reader who wonders why an ingest stage has percentiles
 * and a query node does not finds the answer on the same screen.
 */
export function PipelineDeclarationPanel({ data }: { data: PipelinesResponse }): ReactElement {
  return (
    <section className="space-y-4">
      <SectionHeader
        eyebrow="GET /pipelines · verified against the code it describes"
        title="How the work flows"
        right={
          <InfoTip label="Why this is a contract and not a drawing">
            The server checks this against the ingest stage tuple, the agent graph&rsquo;s
            own node labels and the retrieval observability model before serving it — so
            it cannot drift from the code the way a second copy of the list would.
          </InfoTip>
        }
      />
      <Card>
        <CardHeader eyebrow="one declaration, four consumers" title="Where a stage's output goes" />
        <CardBody>
          <dl className="divide-y divide-border/60">
            {Object.entries(data.channels).map(([channel, meaning]) => (
              <div key={channel} className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0 sm:flex-row sm:gap-3">
                <dt className="shrink-0">
                  <Badge tone={CHANNEL_TONE[channel as EmissionChannel] ?? 'neutral'}>
                    {channel}
                  </Badge>
                </dt>
                <dd className="text-[0.78rem] leading-snug text-muted-foreground">{meaning}</dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>
      {data.pipelines.map((pipeline) => (
        <PipelineCard key={pipeline.name} pipeline={pipeline} />
      ))}
    </section>
  )
}
