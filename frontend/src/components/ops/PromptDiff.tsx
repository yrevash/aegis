import { FileDiff } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import { PanelHead } from './PanelHead'

/** One side of the diff: a prompt body plus whether it is illustrative. */
export interface DiffSide {
  version: number
  text: string | null
  /** True when the body is an illustrative sample (not returned by the API). */
  sample: boolean
}

interface Props {
  base: DiffSide | null
  target: DiffSide | null
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
          <Badge variant="outline" className="ml-auto text-[0.54rem]">
            sample
          </Badge>
        )}
      </div>
      {side.text == null ? (
        <p className="rounded-lg border border-dashed border-border/70 p-3 text-xs text-muted-foreground">
          The API does not expose the prompt body for this version.
        </p>
      ) : (
        <pre className="overflow-auto rounded-lg border border-border bg-surface-2/40 p-3 font-mono text-[0.68rem] leading-relaxed">
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
                <span className="mr-1 select-none opacity-60">{differs ? (mode === 'removed' ? '-' : '+') : ' '}</span>
                {line || ' '}
              </div>
            )
          })}
        </pre>
      )}
    </div>
  )
}

/**
 * A side-by-side **diff** between two prompt versions. Lines present on only one
 * side are highlighted (red = removed from base, green = added in the proposal),
 * so a reviewer sees exactly what the self-improvement loop changed.
 */
export function PromptDiff({ base, target }: Props): ReactElement {
  const baseSet = new Set((base?.text ?? '').split('\n').map((l) => l.trim()).filter(Boolean))
  const targetSet = new Set((target?.text ?? '').split('\n').map((l) => l.trim()).filter(Boolean))

  return (
    <>
      <PanelHead
        icon={FileDiff}
        tint="bg-ml/12"
        ink="text-ml-ink"
        title="Diff"
        subtitle="What the loop changed"
        right={
          base && target ? (
            <span className="eyebrow text-[0.58rem]">
              v{base.version} → v{target.version}
            </span>
          ) : undefined
        }
      />
      {!base || !target ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          Select two versions on the timeline to compare them.
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <DiffColumn side={base} otherLines={targetSet} mode="removed" />
          <DiffColumn side={target} otherLines={baseSet} mode="added" />
        </div>
      )}
    </>
  )
}
