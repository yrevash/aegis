/**
 * The legend's own state: which bands of a stack the reader has switched off.
 *
 * A legend entry rendered as a `<button>` is a promise that clicking it does something.
 * `StackedArea`'s entries carried `onMouseEnter`/`onFocus` only, so a click fired no
 * request, changed no DOM and moved no pixel of the SVG — the exact shape of control the
 * console's own doctrine refuses elsewhere. The obvious thing a chart legend does is
 * toggle its series, so that is what it now does, and the state lives here so the
 * behaviour is assertable without a DOM.
 *
 * Hiding *every* band is allowed. Refusing the last one would put back a click that does
 * nothing, which is the defect being fixed; an empty plot under a legend of struck-through
 * labels reads as "you hid them all" and one more click restores it.
 */

/** Toggle one series key in the hidden set, returning a new set (never mutating). */
export function toggleSeries(hidden: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(hidden)
  if (!next.delete(key)) next.add(key)
  return next
}

/**
 * The series still drawn, in the caller's order.
 *
 * Filtering happens at render time only — the colour ramp is indexed off the *full*
 * series list, so hiding a band never re-colours the ones left standing.
 */
export function shownSeries<T extends { key: string }>(
  series: readonly T[],
  hidden: ReadonlySet<string>,
): T[] {
  return series.filter((s) => !hidden.has(s.key))
}
