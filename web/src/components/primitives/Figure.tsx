import type { ReactElement, ReactNode } from 'react'

import { cn } from '@/lib/utils'

/** The three sizes a figure is allowed to be. See {@link Figure}. */
export type FigureSize = 'body' | 'stat' | 'display'

/**
 * The type ramp for a figure. Mono at every step, per DESIGN.md §3 — Inter is
 * for the interface, JetBrains Mono is for every numeral and identifier.
 */
const SIZE: Record<FigureSize, string> = {
  body: 'text-[0.8125rem] leading-5 font-normal',
  stat: 'text-xl leading-7 font-semibold tracking-[-0.01em]',
  display: 'text-[1.75rem] leading-8 font-semibold tracking-[-0.02em]',
}

interface FigureProps {
  /**
   * The already-formatted figure or identifier. This primitive sets type; it
   * never formats — a component that rounds a spend cap on its way to the screen
   * is a component that has to be audited, and there are enough of those.
   */
  children: ReactNode
  /**
   * `body` (default) — inline in a sentence, a table cell, a chip.
   * `stat` — the number on a stat tile.
   * `display` — the one hero figure a screen is allowed. A second one on the
   * same page is a hierarchy failure, not emphasis (DESIGN.md §3).
   */
  size?: FigureSize
  /**
   * Unit or suffix — `USD`, `ms`, `/1M`. Set quieter and never in the numeral's
   * weight, so a column of figures still aligns on the digits.
   */
  unit?: ReactNode
  /**
   * What a screen reader should say when the glyphs alone are ambiguous —
   * `1.2k`, `p95`, a truncated run id.
   */
  label?: string
  /**
   * Ellipsise an identifier too long for its slot.
   *
   * **This has to be a prop, and `className="truncate"` on a `Figure` silently does
   * nothing.** `truncate` is `overflow:hidden; text-overflow:ellipsis;
   * white-space:nowrap`, and `text-overflow` applies to a *block container's* own inline
   * content — it does not cross into a flex formatting context. This element is an
   * `inline-flex`, so the class landed on the wrong box: the text clipped mid-glyph with
   * no ellipsis, at no measurable document overflow, which is exactly why an
   * overflow-only sweep never saw it. Three call sites had written it that way — a KPI
   * value, a node id and a model id, the last of which cut `Llama-3.2-90B-Visi` in half
   * on a phone.
   *
   * Setting it puts the clip on an inner box that *is* blockified (a flex item), where
   * the ellipsis renders. `tests/design/figureTruncate.test.mjs` fails the build if the
   * class is ever written on a `Figure` again.
   *
   * Pair it with a `title` on the wrapper: an ellipsis without a way to read the whole
   * value is a fact the console has taken away.
   */
  truncate?: boolean
  className?: string
}

/**
 * Every numeral, id, cost, count and timestamp in the console.
 *
 * Two things are non-negotiable in a console and both were being reapplied by
 * hand, inconsistently, on every screen. The first is **mono**: a figure set in
 * Inter next to a figure set in JetBrains Mono reads as two different kinds of
 * fact. The second is **tabular numerics**: without `font-variant-numeric:
 * tabular-nums` a proportional `1` is narrower than a `0`, so a column of costs
 * does not line up and a live counter jitters sideways every time it ticks —
 * which, on a governance figure, reads as a figure that is not quite settled.
 *
 * The `.tabular` utility in `globals.css` carries both `tabular-nums` and the
 * `tnum` feature flag for the faces that only honour the older switch.
 *
 * @example
 * <Figure>{formatUsd(spend)}</Figure>
 * <Figure size="stat" unit="USD">{cap.toFixed(2)}</Figure>
 * <Figure label="run 3f9a2c">{runId.slice(0, 8)}</Figure>
 */
export function Figure({
  children,
  size = 'body',
  unit,
  label,
  truncate = false,
  className,
}: FigureProps): ReactElement {
  return (
    <span
      data-slot="figure"
      aria-label={label}
      className={cn(
        'tabular inline-flex items-baseline gap-1 font-mono',
        SIZE[size],
        // Without a ceiling the inline-flex box takes its max-content width and simply
        // paints past its tile; the clip below would then never engage.
        truncate && 'max-w-full',
        className,
      )}
    >
      {truncate ? <span className="min-w-0 truncate">{children}</span> : children}
      {unit == null ? null : (
        <span className="text-[0.75em] font-normal text-muted-foreground">{unit}</span>
      )}
    </span>
  )
}

export type { FigureProps }
