/**
 * The trust stack, drawn as the path a single request takes.
 *
 * The stages are the real sequence in the agent graph. Earlier passes rendered
 * them as six cards or six paragraphs, which read as an unordered feature list —
 * the ordering *is* the information, so it is now a numbered rail with the
 * request travelling down it, and each stage is three words.
 *
 * The gate is highlighted because it is the claim that distinguishes this from
 * an agent framework: a run stops on a tool's risk tier, not on model confidence.
 */

const STAGES = [
  { n: '01', label: 'Input rails', note: 'injection · PII · schema', tone: 'block' },
  { n: '02', label: 'Retrieval', note: 'cited · provenance', tone: 'graph' },
  { n: '03', label: 'Signal', note: 'conformal · SHAP', tone: 'ml' },
  { n: '04', label: 'Human gate', note: 'by tool risk tier', tone: 'risk' },
  { n: '05', label: 'Governance', note: 'budget · RLS', tone: 'agent' },
  { n: '06', label: 'Audit', note: 'OTel · append-only', tone: 'ok' },
] as const

const DOT: Record<string, string> = {
  block: 'bg-block',
  graph: 'bg-graph',
  ml: 'bg-ml',
  risk: 'bg-risk',
  agent: 'bg-agent',
  ok: 'bg-ok',
}

export function TrustStack() {
  return (
    <section id="trust" className="border-b border-border">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <div className="mb-14 text-center">
          <p className="eyebrow mb-3">Trust</p>
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">
            Six checkpoints between the model and a real action.
          </h2>
        </div>

        <div className="relative mx-auto max-w-4xl">
          {/* the rail the request travels along */}
          <div
            aria-hidden
            className="absolute left-[7px] top-2 bottom-2 w-px bg-border sm:left-0 sm:right-0 sm:top-[7px] sm:bottom-auto sm:h-px sm:w-auto"
          />

          <ol className="relative grid gap-7 sm:grid-cols-6 sm:gap-3">
            {STAGES.map((s) => (
              <li key={s.n} className="flex gap-4 sm:block">
                <span
                  aria-hidden
                  className={`mt-1 size-[15px] shrink-0 rounded-full ring-4 ring-background ${DOT[s.tone]}`}
                />
                <div className="sm:mt-5">
                  <span className="font-mono text-[0.62rem] tracking-[0.1em] text-muted-foreground">
                    {s.n}
                  </span>
                  <p className="mt-0.5 text-[0.9rem] font-semibold tracking-tight text-foreground">
                    {s.label}
                  </p>
                  <p className="mt-1 font-mono text-[0.66rem] leading-relaxed text-muted-foreground">
                    {s.note}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        <p className="mx-auto mt-14 max-w-lg text-center text-[0.95rem] leading-relaxed text-foreground">
          The gate fires on a tool&rsquo;s <strong className="font-semibold">risk tier</strong> —
          never on model confidence.
        </p>
      </div>
    </section>
  )
}
