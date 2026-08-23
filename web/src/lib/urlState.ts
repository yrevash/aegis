'use client'

/**
 * One query parameter, held in the URL instead of in a `useState` nobody can link to.
 *
 * Every filter in this console is local component state, which is right for a filter:
 * "In flight" is a lens somebody holds for ten seconds, not a place. **What a person
 * was sent to is a place.** An alert that says "policy-4.pdf failed" has to be able to
 * open *that document's* log — and a screen whose entire selection lives in React state
 * cannot be addressed, cannot be reloaded, and cannot be sent to a colleague. So the
 * one piece of state a link carries is kept where links keep things.
 *
 * Three properties follow, and they are the whole reason this is a hook rather than a
 * `useState` beside the others:
 *
 * - **The URL reflects the state.** Opening a job's log rewrites `?document=`, so the
 *   address bar always says what is open. Closing it removes the parameter rather than
 *   leaving `?document=` behind — an empty parameter is a state the reader can reach
 *   and the code then has to defend against.
 * - **A deep link works.** The parameter is read on the first render, so arriving from
 *   the bell lands with the entity already selected; there is no flash of the unfiltered
 *   list followed by a jump.
 * - **It survives a reload.** F5 is a fresh mount reading the same URL.
 *
 * `router.replace`, never `push`: opening and closing a log is not four entries in the
 * reader's Back history, and Back should return them to where the alert came from. And
 * `scroll: false`, because these screens scroll the selected row into view themselves —
 * Next's default jump to the top would undo that on every keystroke of state.
 *
 * ## The Suspense requirement
 *
 * `useSearchParams` opts its subtree out of prerendering, and `next build` fails on any
 * statically generated page that calls it outside a `<Suspense>` boundary — the portal's
 * `[role]/[section]` page is one, since `generateStaticParams` enumerates every combo.
 * The boundary therefore lives in each mount component (`JobsMount`, `ApprovalsMount`)
 * rather than being left to the caller to remember, so a screen adopting this hook
 * cannot break the build from a distance.
 */

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

/** Read/write one query parameter. `null` means absent — never the empty string. */
export function useUrlParam(name: string): [string | null, (next: string | null) => void] {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const value = params.get(name)

  const set = useCallback(
    (next: string | null): void => {
      const draft = new URLSearchParams(params.toString())
      if (next === null || next === '') draft.delete(name)
      else draft.set(name, next)
      const query = draft.toString()
      router.replace(query === '' ? pathname : `${pathname}?${query}`, { scroll: false })
    },
    [name, params, pathname, router],
  )

  return [value === '' ? null : value, set]
}

/**
 * The same, for a parameter that must be a positive integer.
 *
 * `?document=abc` is not a document; it is a typo or a probe, and treating it as one
 * would send `NaN` to an API path. It resolves to `null` — the screen then behaves as
 * if no deep link were present, which is the honest reading of a target that names
 * nothing.
 */
export function useUrlIdParam(name: string): [number | null, (next: number | null) => void] {
  const [raw, setRaw] = useUrlParam(name)
  const parsed = raw === null ? null : Number(raw)
  const value = parsed !== null && Number.isInteger(parsed) && parsed > 0 ? parsed : null
  const set = useCallback(
    (next: number | null): void => setRaw(next === null ? null : String(next)),
    [setRaw],
  )
  return [value, set]
}
