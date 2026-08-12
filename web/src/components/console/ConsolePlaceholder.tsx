import { Sparkles, CornerDownLeft } from 'lucide-react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'

/**
 * Console placeholder — the ai_team landing surface. Renders the query bar +
 * the "live run will render here" panel. The real SSE wiring (openQueryStream +
 * readSSEStream → trace / graph / guardrail panels) lands in the next task; the
 * typed client and decoder it will use already exist under `@/lib/api`.
 */
export function ConsolePlaceholder() {
  return (
    <div className="space-y-6">
      {/* Query bar (disabled stub) */}
      <Card className="p-2">
        <div className="flex items-center gap-3 px-3 py-2">
          <Sparkles className="size-5 shrink-0 text-agent-ink" />
          <input
            disabled
            placeholder="Ask Aegis…  (live run wiring lands next task)"
            className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          <span className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 px-2 py-1 text-xs text-muted-foreground">
            <CornerDownLeft className="size-3.5" /> Run
          </span>
        </div>
      </Card>

      {/* Live-run canvas */}
      <div
        className="flex min-h-[420px] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-surface-2/40 text-center"
      >
        <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-agent/20 text-agent-ink">
          <Sparkles className="size-6" />
        </span>
        <p className="t-title text-foreground">Live run will render here</p>
        <p className="max-w-md text-sm text-muted-foreground">
          The reasoning trace, knowledge-graph animation, guardrail verdicts, ML explanation and
          final answer stream into this canvas once the SSE console is wired.
        </p>
        <div className="mt-1 flex flex-wrap justify-center gap-2">
          <Badge tone="agent">reasoning</Badge>
          <Badge tone="graph">retrieval</Badge>
          <Badge tone="block">guardrail</Badge>
          <Badge tone="ml">ml_explanation</Badge>
          <Badge tone="ok">run_finished</Badge>
        </div>
      </div>
    </div>
  )
}
