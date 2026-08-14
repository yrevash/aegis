import Image from 'next/image'
import Link from 'next/link'

/**
 * The opening statement: one claim, one line, two ways in, and the product
 * itself.
 *
 * The screenshot is the argument — an agentic platform that says it shows its
 * work should show its work above the fold rather than describe it in prose.
 *
 * The shot is the console running on offline demo data, and the red banner in it
 * saying so is left visible on purpose. Cropping that out would dress mock
 * figures up as production ones, which is the exact claim this product refuses
 * to make anywhere else.
 */
export function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border bg-surface">
      <div className="mx-auto max-w-6xl px-6 pt-20 pb-0 text-center">
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

        {/* The product, framed like a window and bled off the bottom edge. */}
        <div className="mx-auto mt-16 max-w-5xl">
          <div className="overflow-hidden rounded-t-xl border border-b-0 border-border bg-card shadow-[0_-1px_60px_-15px_rgba(16,24,40,0.18)]">
            <div className="flex items-center gap-1.5 border-b border-border bg-surface-2 px-4 py-3">
              <span className="size-2.5 rounded-full bg-block" />
              <span className="size-2.5 rounded-full bg-risk" />
              <span className="size-2.5 rounded-full bg-ok" />
            </div>
            <Image
              src="/shots/console.png"
              alt="The Aegis console streaming an agent run, showing the reasoning lane, retrieval and the approval gate"
              width={2880}
              height={1800}
              priority
              className="w-full"
            />
          </div>
        </div>
      </div>
    </section>
  )
}
