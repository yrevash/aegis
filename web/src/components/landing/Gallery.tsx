import Image from 'next/image'

/**
 * Real console surfaces — the "show your work" claim, shown.
 *
 * Every image is a screenshot of the actual running console. They are captured
 * on **offline demo data**, and each one carries the console's own red
 * "OFFLINE DEMO — MOCK DATA" banner. That banner is deliberately not cropped:
 * removing it would present demo figures as production ones, and a platform
 * whose whole pitch is honest instrumentation cannot fake its own marketing.
 */

const SHOTS = [
  {
    src: '/shots/overview.png',
    title: 'Command centre',
    note: 'Spend, approvals, security posture and latency in one view.',
  },
  {
    src: '/shots/graph.png',
    title: 'Knowledge graph',
    note: 'The entities a run touched, drawn from Neo4j.',
  },
  {
    src: '/shots/guardrails.png',
    title: 'Guardrails',
    note: 'Six layers, each with its own pass/block record.',
  },
  {
    src: '/shots/memory.png',
    title: 'Memory',
    note: 'Episodic, semantic and procedural recall, with the debug trail.',
  },
]

export function Gallery() {
  return (
    <section id="console" className="border-b border-border bg-surface">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <div className="mb-12 text-center">
          <p className="eyebrow mb-3">The console</p>
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">
            Every claim has a screen behind it.
          </h2>
        </div>

        <div className="grid gap-6 sm:grid-cols-2">
          {SHOTS.map((s) => (
            <figure
              key={s.src}
              className="overflow-hidden rounded-xl border border-border bg-card"
            >
              <Image
                src={s.src}
                alt={`Aegis console — ${s.title}`}
                width={2880}
                height={1800}
                className="w-full border-b border-border"
              />
              <figcaption className="px-5 py-4">
                <p className="text-sm font-semibold tracking-tight text-foreground">
                  {s.title}
                </p>
                <p className="mt-0.5 text-[0.78rem] text-muted-foreground">{s.note}</p>
              </figcaption>
            </figure>
          ))}
        </div>

        <p className="mt-6 text-center font-mono text-[0.68rem] text-muted-foreground">
          Captured on offline demo data — the banner in each shot says so.
        </p>
      </div>
    </section>
  )
}
