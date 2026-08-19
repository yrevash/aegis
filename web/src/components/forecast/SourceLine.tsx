'use client'

import type { ReactElement } from 'react'

/**
 * The `Source:` footer under a panel — what these numbers came from, in one line.
 *
 * Small, mono and always present. A panel without one is a panel a reader has to
 * guess about, and on this page the guess a reader makes is the wrong one: the two
 * panels are two models, and only their sources say so.
 */
export function SourceLine({
  source,
  detail,
}: {
  /** The provenance string itself, e.g. `Source: usage_ledger · …`. */
  source: string
  /** Measured detail this particular render can add — never a claim, always a fact. */
  detail?: string | null
}): ReactElement {
  return (
    <p className="tabular border-t border-border pt-3 font-mono text-[0.7rem] leading-relaxed text-muted-foreground">
      <span className="text-foreground">{source}</span>
      {detail ? <span> · {detail}</span> : null}
    </p>
  )
}
