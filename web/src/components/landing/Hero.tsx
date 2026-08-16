import Link from 'next/link'

/**
 * The opening statement: one claim, one line, two ways in.
 *
 * There is deliberately no product screenshot here. The shot this section used to
 * carry was captured against the in-browser mock, so every figure in it — spend,
 * approvals, the case it was resolving — was invented, and the console banner
 * saying so was part of the image. A platform whose pitch is honest
 * instrumentation cannot lead with a picture of numbers it never measured, so the
 * section makes its claim in words and sends the visitor to the live console for
 * the proof. A shot captured against a real backend can take this place.
 */
export function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border bg-surface">
      <div className="mx-auto max-w-6xl px-6 py-28 text-center">
        <p className="eyebrow mb-5">Bounded-autonomy AI, made watchable</p>
        <h1 className="mx-auto max-w-4xl text-balance text-5xl font-semibold leading-[1.05] tracking-tight text-foreground sm:text-[4.2rem]">
          Autonomy you can audit.
        </h1>
        <p className="mx-auto mt-6 max-w-xl text-lg text-muted-foreground">
          Agents that take real actions — and prove every one of them.
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/login"
            className="inline-flex h-11 items-center rounded-md bg-primary px-6 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            Enter the console
          </Link>
          <a
            href="#architecture"
            className="inline-flex h-11 items-center rounded-md border border-border bg-card px-6 text-sm font-medium text-foreground transition-colors hover:bg-muted"
          >
            How it works
          </a>
        </div>
      </div>
    </section>
  )
}
