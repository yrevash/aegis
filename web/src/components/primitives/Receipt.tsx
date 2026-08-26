import { CircleSlash } from 'lucide-react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { InfoTip } from './InfoTip'
import { receiptText, splitDetail } from './receiptText'

export { receiptText, splitDetail, type ReceiptText } from './receiptText'

interface ReceiptProps {
  /**
   * The lead-in word. `Source` by default; a clamped cap says `Decided by`, a
   * health row says `Evidence`, a caught attack says `Rail`.
   */
  label?: string
  /** Where the figure came from — a table, a model, a rail, a person. */
  origin: string
  /** Measured detail this render can add. Never a claim, always a fact. */
  detail?: string | null
  /**
   * `block` (default) sits at the foot of a panel, under a hairline rule.
   * `inline` sits beside the figure it explains, with no rule.
   */
  variant?: 'block' | 'inline'
  className?: string
}

/**
 * The receipt — where a figure came from, in the one treatment used everywhere.
 *
 * Aegis's product thesis is that nothing is asserted without its origin, and
 * that discipline had been re-improvised on every screen: a `Source:` footer on
 * the forecast panels, evidence text on a health row, the rail that caught a
 * red-team attack, `decided_by` under a clamped spend cap. Same idea, six
 * spellings, so a reader had to learn each one before they could trust it.
 *
 * This is that idea as one component (DESIGN.md §1): `text-xs`, muted, mono for
 * the identifiers, and a hairline rule above when it closes a panel. It is the
 * only place the system spends visual boldness, which is why everything around
 * it stays quiet.
 *
 * @example
 * <Receipt origin="usage_ledger · univariate · statsforecast" detail="n=412" />
 * <Receipt label="Decided by" origin="platform default" variant="inline" />
 */
export function Receipt({
  label = 'Source',
  origin,
  detail,
  variant = 'block',
  className,
}: ReceiptProps): ReactElement {
  const { inline: inlineDetail, tip: tipDetail } = splitDetail(detail)
  const text = receiptText(label, origin, inlineDetail)
  return (
    <p
      data-slot="receipt"
      className={cn(
        'tabular font-mono text-xs leading-relaxed break-words text-muted-foreground',
        variant === 'block' && 'border-t border-border pt-3',
        className,
      )}
    >
      <span className="text-foreground">{text.label}:</span>{' '}
      <span translate="no">{text.body}</span>
      {tipDetail ? (
        <>
          {' '}
          <InfoTip label={`What ${text.label.toLowerCase()} means here`} className="align-text-bottom">
            {tipDetail}
          </InfoTip>
        </>
      ) : null}
    </p>
  )
}

interface AbsenceProps {
  /** The figure that would have gone here, named exactly as a reader expects it. */
  figure: string
  /** Why it is not recorded. The honest reason, not an apology. */
  why: string
  /** What would have to be emitted or stored before the figure could exist. */
  needed?: string
  className?: string
}

/**
 * The other half of the receipt: a figure that cannot be sourced, stated.
 *
 * A dashboard's silences are invisible — a reader cannot tell a figure that is
 * missing from one that was never possible, so they invent the missing one.
 * DESIGN.md §1 is unambiguous about the fix: a figure that cannot be sourced is
 * never rendered as a number, it is a **stated absence in the slot the number
 * would have occupied**. That is what this renders, and it is why it lives in
 * the same file as {@link Receipt} rather than in a screen.
 *
 * **One line, not three** (DESIGN.md §4). This used to stack `figure`, `why` and
 * `needed` as three `<p>`s, and with 100-odd of them across the console that is
 * the single biggest source of the "too much text" the reviewers hit: a panel
 * with nothing to show was taller than a panel with something to show. So the
 * figure and its reason join on one line, and `needed` — which is a recipe, not
 * a state — moves behind an {@link InfoTip}, which §4 requires stay reachable by
 * hover *and* by keyboard. Nothing is deleted; a fix here fixes every call site.
 */
export function Absence({ figure, why, needed, className }: AbsenceProps): ReactElement {
  return (
    <div
      data-slot="absence"
      className={cn('flex items-start gap-2 rounded-md border border-border bg-surface-2/40 p-3', className)}
    >
      <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <p className="min-w-0 text-xs leading-relaxed break-words text-muted-foreground">
        <span className="font-semibold text-foreground">{figure}</span>
        <span aria-hidden> · </span>
        {why}
        {needed == null ? null : (
          <>
            {' '}
            <InfoTip label={`To measure ${figure}`} className="align-text-bottom">
              {needed}
            </InfoTip>
          </>
        )}
      </p>
    </div>
  )
}

export type { ReceiptProps, AbsenceProps }
