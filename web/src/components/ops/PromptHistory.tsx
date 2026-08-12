'use client'

import { FileDiff, GitBranch, Loader2 } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { cn } from '@/lib/utils'
import type { OpsActivePromptResponse, OpsPromptVersionRow } from '@/lib/api/ops'

import { SAMPLE_BODIES, formatAgo } from './opsShared'

const STATUS_STYLE: Record<string, { tone: BadgeTone; dot: string }> = {
  active: { tone: 'ok', dot: 'bg-ok' },
  staged: { tone: 'risk', dot: 'bg-risk' },
  draft: { tone: 'graph', dot: 'bg-graph' },
  archived: { tone: 'neutral', dot: 'bg-border' },
}

/** One side of the diff: a prompt body plus whether it is illustrative. */
interface DiffSide {
  version: number
  text: string | null
  sample: boolean
}

/** One version row on the timeline (click to add/remove from the diff pair). */
function VersionRow({
  row,
  role,
  onSelect,
}: {
  row: OpsPromptVersionRow
  role: 'base' | 'target' | null
  onSelect: (v: number) => void
}): ReactElement {
  const style = STATUS_STYLE[row.status] ?? STATUS_STYLE.archived
  return (
    <li className="relative flex gap-3">
      <span
        className={cn(
          'z-10 mt-1 grid size-4 shrink-0 place-items-center rounded-full border-2 border-card',
          style.dot,
        )}
      />
      <button
        type="button"
        onClick={() => onSelect(row.version)}
        className={cn(
          'min-w-0 flex-1 rounded-xl border p-3 text-left transition-colors',
          role
            ? 'border-primary/40 bg-surface-2/60 ring-1 ring-primary/20'
            : 'border-border bg-card hover:bg-surface-2/40',
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="tabular font-display text-sm font-semibold text-foreground">v{row.version}</span>
          <Badge tone={style.tone} className="text-[0.58rem]">
            {row.status}
          </Badge>
          {role && (
            <span className="eyebrow rounded-sm bg-foreground px-1.5 py-0.5 text-[0.54rem] text-background">
              diff {role}
            </span>
          )}
          <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(row.created_at)}</span>
        </div>
        {row.notes && <p className="mt-1 text-xs leading-snug text-muted-foreground">{row.notes}</p>}
        <p className="mt-1 font-mono text-[0.6rem] text-muted-foreground/80">by {row.created_by ?? 'system'}</p>
      </button>
    </li>
  )
}

/** A single diff column with per-line add/remove highlighting. */
function DiffColumn({
  side,
  otherLines,
  mode,
}: {
  side: DiffSide
  otherLines: Set<string>
  mode: 'removed' | 'added'
}): ReactElement {
  const lines = (side.text ?? '').split('\n')
  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center gap-2">
        <span className="tabular font-display text-sm font-semibold text-foreground">v{side.version}</span>
        <span className="eyebrow text-[0.56rem]">{mode === 'removed' ? 'base' : 'proposed'}</span>
        {side.sample && (
          <Badge tone="neutral" className="ml-auto text-[0.54rem]">
            sample
          </Badge>
        )}
      </div>
      {side.text == null ? (
        <p className="rounded-xl border border-dashed border-border/70 p-3 text-xs text-muted-foreground">
          The API does not expose the prompt body for this version.
        </p>
      ) : (
        <pre className="overflow-auto rounded-xl border border-border bg-surface-2/40 p-3 font-mono text-[0.68rem] leading-relaxed">
          {lines.map((line, i) => {
            const differs = line.trim() !== '' && !otherLines.has(line.trim())
            return (
              <div
                key={i}
                className={cn(
                  'whitespace-pre-wrap px-1',
                  differs && (mode === 'removed' ? 'bg-block/10 text-block-ink' : 'bg-ok/10 text-ok-ink'),
                  !differs && 'text-muted-foreground',
                )}
              >
                <span className="mr-1 select-none opacity-60">
                  {differs ? (mode === 'removed' ? '-' : '+') : ' '}
                </span>
                {line || ' '}
              </div>
            )
          })}
        </pre>
      )}
    </div>
  )
}

interface Props {
  rows: OpsPromptVersionRow[]
  active: OpsActivePromptResponse | null
  loading: boolean
  error: string | null
}

/**
 * The **Prompt history** — every version of the tracked prompt with its
 * lifecycle status (draft / staged / active / archived), and a side-by-side diff
 * of any two. The active version's body is the real one (`/ops/prompts/active`);
 * other bodies are illustrative samples, badged as such. Tap two versions to
 * compare; red = removed from base, green = added in the proposal.
 */
export function PromptHistory({ rows, active, loading, error }: Props): ReactElement {
  const activeVersion = active?.version ?? null
  const stagedVersion = rows.find((r) => r.status === 'staged')?.version ?? null

  const [base, setBase] = useState<number | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const effBase = base ?? activeVersion
  const effTarget = target ?? stagedVersion

  const bodyFor = useMemo(
    () =>
      (version: number | null): DiffSide | null => {
        if (version == null) return null
        if (version === activeVersion && active?.system_prompt != null) {
          return { version, text: active.system_prompt, sample: false }
        }
        const text = SAMPLE_BODIES[version] ?? null
        return { version, text, sample: text != null }
      },
    [activeVersion, active],
  )

  // Tap a version to fill the diff pair (base first, then target; tap again clears).
  const selectVersion = (v: number): void => {
    if (v === effBase) {
      setBase(effTarget ?? null)
      setTarget(null)
      return
    }
    if (v === effTarget) {
      setTarget(null)
      return
    }
    if (effBase == null) setBase(v)
    else setTarget(v)
  }

  const baseSide = bodyFor(effBase)
  const targetSide = bodyFor(effTarget)
  const baseSet = new Set((baseSide?.text ?? '').split('\n').map((l) => l.trim()).filter(Boolean))
  const targetSet = new Set((targetSide?.text ?? '').split('\n').map((l) => l.trim()).filter(Boolean))

  return (
    <Card>
      <CardHeader
        eyebrow="GET /ops/prompts"
        title="Prompt history"
        description="Every version with its lifecycle status, and a diff of what the loop changed. Tap two versions to compare."
      />
      <CardBody>
        {error ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : loading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading versions…
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,20rem)_1fr]">
            {/* Timeline */}
            <div>
              <div className="mb-3 flex items-center gap-2">
                <span className="grid size-7 place-items-center rounded-lg bg-graph/12">
                  <GitBranch className="size-4 text-graph-ink" />
                </span>
                <h4 className="t-label text-foreground">Versions</h4>
                <span className="eyebrow ml-auto text-[0.56rem]">tap two to diff</span>
              </div>
              {rows.length === 0 ? (
                <p className="py-8 text-sm text-muted-foreground">No prompt versions recorded yet.</p>
              ) : (
                <ol className="relative space-y-2.5 before:absolute before:top-2 before:bottom-2 before:left-[7px] before:w-0.5 before:bg-border/70">
                  {rows.map((row) => (
                    <VersionRow
                      key={row.id}
                      row={row}
                      role={row.version === effBase ? 'base' : row.version === effTarget ? 'target' : null}
                      onSelect={selectVersion}
                    />
                  ))}
                </ol>
              )}
            </div>

            {/* Diff */}
            <div>
              <div className="mb-3 flex items-center gap-2">
                <span className="grid size-7 place-items-center rounded-lg bg-ml/12">
                  <FileDiff className="size-4 text-ml-ink" />
                </span>
                <h4 className="t-label text-foreground">Diff</h4>
                {baseSide && targetSide && (
                  <span className="eyebrow ml-auto text-[0.58rem]">
                    v{baseSide.version} → v{targetSide.version}
                  </span>
                )}
              </div>
              {!baseSide || !targetSide ? (
                <div className="flex h-full min-h-[200px] items-center justify-center rounded-xl border border-dashed border-border/70 py-8 text-center text-sm text-muted-foreground">
                  Select two versions on the timeline to compare them.
                </div>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  <DiffColumn side={baseSide} otherLines={targetSet} mode="removed" />
                  <DiffColumn side={targetSide} otherLines={baseSet} mode="added" />
                </div>
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}
