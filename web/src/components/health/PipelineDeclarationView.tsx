'use client'

import { type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
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
      <TD className="tabular whitespace-nowrap text-[0.75rem] text-muted-foreground">
        {index + 1}
      </TD>
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="font-mono text-[0.78rem] font-medium text-foreground">
            {stage.name}
          </span>
          <span className="text-[0.7rem] text-muted-foreground">{stage.label}</span>
          {stage.optional ? (
            <span className="pt-0.5">
              <Badge tone="neutral">conditional</Badge>
            </span>
          ) : null}
        </div>
      </TD>
      <TD>
        <div className="flex max-w-md flex-col gap-1">
          <span className="text-[0.8rem] leading-snug text-foreground">{stage.summary}</span>
          <span className="font-mono text-[0.68rem] leading-snug text-muted-foreground">
            {stage.owner}
          </span>
        </div>
      </TD>
      <TD>
        {stage.emits.length === 0 ? (
          <span className="text-[0.75rem] text-muted-foreground">nothing observable</span>
        ) : (
          <ul className="flex max-w-lg flex-col gap-1.5">
            {stage.emits.map((emission) => (
              <li key={`${emission.channel}:${emission.name}`} className="flex flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-1.5">
                  <Badge tone={CHANNEL_TONE[emission.channel] ?? 'neutral'}>
                    {emission.channel}
                  </Badge>
                  <span className="font-mono text-[0.72rem] text-foreground">
                    {emission.name}
                  </span>
                </span>
                <span className="text-[0.72rem] leading-snug text-muted-foreground">
                  {emission.detail}
                </span>
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
    <Card>
      <CardHeader
        eyebrow={`${pipeline.stages.length} stages · ${pipeline.entrypoint}`}
        title={pipeline.title}
      />
      <CardBody className="space-y-4">
        <p className="max-w-3xl text-sm leading-snug text-foreground">{pipeline.summary}</p>
        <div className="flex flex-wrap items-center gap-2">
          {pipeline.durable_record ? (
            <Badge tone="ok">persisted to {pipeline.durable_record}</Badge>
          ) : (
            <Badge tone="neutral">nothing persisted</Badge>
          )}
        </div>
        <Table>
          <THead>
            <TH className="w-8">#</TH>
            <TH>Stage</TH>
            <TH>What it does · who owns it</TH>
            <TH>What it emits</TH>
          </THead>
          <TBody>
            {pipeline.stages.map((stage, index) => (
              <StageRow key={stage.name} stage={stage} index={index} />
            ))}
          </TBody>
        </Table>
        {pipeline.limits.length > 0 ? (
          <div className="rounded-xl border border-border bg-surface-2 p-4">
            <p className="eyebrow mb-2">What this pipeline does not record</p>
            <ul className="space-y-1.5">
              {pipeline.limits.map((limit) => (
                <li key={limit} className="text-[0.78rem] leading-snug text-muted-foreground">
                  {limit}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardBody>
    </Card>
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
      <div>
        <p className="eyebrow mb-1">GET /pipelines · verified against the code it describes</p>
        <h2 className="t-title text-foreground">How the work flows</h2>
      </div>
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
