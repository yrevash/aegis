/**
 * Small, dependency-free datetime helpers for the Memory surface. The web app
 * has no shared datetime util (see `ops/opsShared.formatAgo`), so the two
 * formatters the ported panels need live here.
 */

/** A compact relative-time label ("3h ago"); null/invalid → em dash. */
export function formatAgo(value: string | null | undefined): string {
  if (!value) return '—'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '—'
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `${days}d ago`
}

/**
 * A short absolute calendar date ("11 Feb 2023"); null/invalid → em dash.
 *
 * One `Intl.DateTimeFormat`, built once, with the locale left undefined: a pinned
 * `en-US` is a hardcoded date format, and constructing a formatter per call is the
 * expensive half of `toLocaleDateString`.
 */
const DATE_FORMAT = new Intl.DateTimeFormat(undefined, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return DATE_FORMAT.format(d)
}
