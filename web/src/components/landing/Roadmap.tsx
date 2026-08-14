import { Check } from 'lucide-react'

/**
 * Production posture as two rows of chips.
 *
 * Split shipped / next so the page never implies a planned capability already
 * exists. Chips rather than bulleted sentences: these are labels, and the
 * previous list read as prose the eye slid off.
 */

const RUNNING = [
  'Multi-tenant RLS + budgets',
  'Durable resumable runs',
  'Native Qdrant + Neo4j',
  'OTel tracing + audit log',
  'Offline + CI eval gates',
]

const NEXT = [
  'Horizontal workers',
  'Managed store tiers',
  'Installable domain packs',
  'Live eval sampling',
]

export function Roadmap() {
  return (
    <section id="roadmap" className="border-b border-border">
      <div className="mx-auto max-w-4xl px-6 py-20 text-center">
        <p className="eyebrow mb-3">Roadmap</p>
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">
          Built for production, with the next steps named.
        </h2>

        <div className="mt-12 space-y-8">
          <div>
            <p className="mb-4 font-mono text-[0.62rem] uppercase tracking-[0.09em] text-ok-ink">
              Running today
            </p>
            <ul className="flex flex-wrap justify-center gap-2">
              {RUNNING.map((t) => (
                <li
                  key={t}
                  className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok/10 px-3.5 py-1.5 text-[0.8rem] text-foreground"
                >
                  <Check aria-hidden className="size-3.5 text-ok-ink" strokeWidth={2.5} />
                  {t}
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="mb-4 font-mono text-[0.62rem] uppercase tracking-[0.09em] text-muted-foreground">
              Next
            </p>
            <ul className="flex flex-wrap justify-center gap-2">
              {NEXT.map((t) => (
                <li
                  key={t}
                  className="rounded-full border border-border px-3.5 py-1.5 text-[0.8rem] text-muted-foreground"
                >
                  {t}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  )
}
