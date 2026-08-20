'use client'

import { FileDiff, GitBranch, Loader2 } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { cn } from '@/lib/utils'
import type { OpsActivePromptResponse, OpsPromptVersionRow } from '@/lib/api/ops'

import { SAMPLE_BODIES, formatAgo } from './opsShared'

const STATUS_STYLE: Record<string, { tone: BadgeTone; dot: string }> = {
  active: { tone: 'ok', dot: 'bg-ok' },
  staged: { tone: 'risk', dot: 'bg-risk' },
  draft: { tone: 'graph', dot: 'bg-blue-400' },
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
          'min-w-0 flex-1 rounded-lg border p-3 text-left transition-colors',
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

/** One line of the unified diff: unchanged, removed from base, or added. */
interface DiffLine {
  kind: ' ' | '-' | '+'
  text: string
}

/**
 * A unified line diff (LCS-backed). Side-by-side columns repeated every shared
 * line twice; the unified form shows each shared line once, so the panel carries
 * the same content with none of the duplication.
 */
function unifiedDiff(base: string[], target: string[]): DiffLine[] {
  const n = base.length
  const m = target.length
  // dp[i][j] = length of the LCS of base[i:] and target[j:].
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        base[i].trim() === target[j].trim()
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (base[i].trim() === target[j].trim()) {
      out.push({ kind: ' ', text: base[i] })
      i += 1
      j += 1
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ kind: '-', text: base[i] })
      i += 1
    } else {
      out.push({ kind: '+', text: target[j] })
      j += 1
    }
  }
  for (; i < n; i += 1) out.push({ kind: '-', text: base[i] })
  for (; j < m; j += 1) out.push({ kind: '+', text: target[j] })
  return out
}

/** The unified diff body — one column, each shared line rendered once. */
function DiffBody({ base, target }: { base: DiffSide; target: DiffSide }): ReactElement {
  if (base.text == null || target.text == null) {
    return (
      <p className="rounded-lg border border-dashed border-border/70 p-3 text-xs text-muted-foreground">
        The API does not expose the prompt body for v{base.text == null ? base.version : target.version}.
      </p>
    )
  }
  const lines = unifiedDiff(base.text.split('\n'), target.text.split('\n'))
  return (
    <pre className="overflow-auto rounded-lg border border-border bg-surface-2/40 p-3 font-mono text-[0.68rem] leading-relaxed">
      {lines.map((line, i) => (
        <div
          key={i}
          className={cn(
            'whitespace-pre-wrap px-1',
            line.kind === '-' && 'bg-block/10 text-block-ink',
            line.kind === '+' && 'bg-ok/10 text-ok-ink',
            line.kind === ' ' && 'text-muted-foreground',
          )}
        >
          <span className="mr-1 select-none opacity-60">{line.kind}</span>
          {line.text || ' '}
        </div>
      ))}
    </pre>
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

  return (
    <Card>
      <CardHeader
        eyebrow="GET /ops/prompts"
        title="Prompt history"
        actions={
          <InfoTip label="About the prompt history">
            Every version of the tracked prompt with its lifecycle status. Tap two versions on the
            timeline to diff them — red is removed from the base, green is added in the proposal.
            Only the active version&rsquo;s body is the real one; the rest are illustrative samples,
            badged as such.
          </InfoTip>
        }
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
                <span className="grid size-7 place-items-center rounded-lg bg-blue-400/12">
                  <GitBranch className="size-4 text-blue-600" />
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
                <span className="grid size-7 place-items-center rounded-lg bg-blue-100/12">
                  <FileDiff className="size-4 text-blue-800" />
                </span>
                <h4 className="t-label text-foreground">Diff</h4>
                {baseSide && targetSide && (
                  <>
                    <span className="eyebrow ml-auto text-[0.58rem]">
                      v{baseSide.version} → v{targetSide.version}
                    </span>
                    {(baseSide.sample || targetSide.sample) && (
                      <Badge tone="neutral" className="text-[0.54rem]">
                        sample
                      </Badge>
                    )}
                  </>
                )}
              </div>
              {!baseSide || !targetSide ? (
                <div className="flex h-full min-h-[200px] items-center justify-center rounded-lg border border-dashed border-border/70 py-8 text-center text-sm text-muted-foreground">
                  Select two versions on the timeline to compare them.
                </div>
              ) : (
                <DiffBody base={baseSide} target={targetSide} />
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}
