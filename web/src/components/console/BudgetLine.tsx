'use client'

import { CircleDollarSign } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { getMyBudget, type MyBudgetResponse } from '@/lib/api/console'
import { cn } from '@/lib/utils'

import { budgetLine } from './composerBudget'

/**
 * The composer's budget line — the caller's own spend against the caller's own cap.
 *
 * It reads `GET /me/budget`, which is the same `BudgetStatusRow` set the gateway
 * compares every call against. That is the whole reason the endpoint exists: a pill
 * reading anything else would eventually disagree with a refusal, and the first person
 * to find the gap would be on a jury.
 *
 * It re-reads when a run finishes rather than on a timer, because that is when the
 * figure can actually have moved, and a pill that polls an unchanged number is a pill
 * that costs a request per tick for nothing.
 *
 * **An unmeasured figure is never drawn as a measurement.** `measured: false` means no
 * cap governs this principal; the line says so instead of printing `$0.00 of $50`.
 * {@link budgetLine} owns that rule.
 */
export function BudgetLine({
  token,
  /** Bumped whenever a run settles, so the figure is re-read exactly then. */
  refreshKey,
}: {
  token: string | null
  refreshKey: number
}): ReactElement | null {
  const [budget, setBudget] = useState<MyBudgetResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    void getMyBudget(token)
      .then((data) => {
        if (!alive) return
        setBudget(data)
        setFailed(false)
      })
      .catch(() => {
        if (!alive) return
        setBudget(null)
        setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [token, refreshKey])

  // A caller who cannot read their own budget at all gets no line rather than an error
  // in the composer: the sentence belongs on the Governance surface, not under the box.
  if (failed) return null

  const line = budgetLine(budget, failed)

  return (
    <p
      className={cn(
        'flex min-w-0 items-center gap-1.5 text-[0.72rem]',
        line.measured ? 'text-muted-foreground' : 'text-muted-foreground/80',
      )}
    >
      <CircleDollarSign aria-hidden className="size-3.5 shrink-0" />
      {/* A measured figure is a figure — mono and tabular through the one primitive that
          owns that, rather than the two utilities reapplied by hand. An unmeasured line
          is a sentence and is set as one. */}
      {line.measured ? (
        <Figure className="min-w-0 text-[0.72rem]">
          {/* The ellipsis lives on a block-level child: `text-overflow` has nothing to
              trim on the inline-flex box `Figure` itself. */}
          <span className="min-w-0 truncate" title={line.text}>
            {line.text}
          </span>
        </Figure>
      ) : (
        <span className="min-w-0 truncate" title={line.text}>
          {line.text}
        </span>
      )}
      {/* Always drawn. It used to be `sm:block`, a viewport breakpoint inside a composer
          that is never as wide as the window. Swapping it for a *container* query
          collapsed the whole line instead: `container-type: inline-size` carries
          `contain: inline-size`, and this paragraph is a flex item that sizes to its
          content, so containing it sized it to nothing. A 64px meter that always shows is
          the honest fix. */}
      {line.meterable && (
        <span
          aria-hidden
          className="h-1 w-16 shrink-0 overflow-hidden rounded-full bg-surface-2"
        >
          <span
            className="block h-full rounded-full bg-blue-200"
            style={{ width: `${Math.round(line.ratio * 100)}%` }}
          />
        </span>
      )}
    </p>
  )
}
