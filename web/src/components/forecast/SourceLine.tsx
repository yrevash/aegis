import type { ReactElement } from 'react'

import { Receipt } from '@/components/primitives/Receipt'

/**
 * The `Source:` footer under a forecast panel.
 *
 * Kept as a named wrapper because the two panels on this page mean two different
 * models and only their sources say so — but the treatment itself is no longer
 * private to this folder. It is {@link Receipt}, the one provenance element the
 * whole console shares (DESIGN.md §1).
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
  return <Receipt origin={source} detail={detail} />
}
