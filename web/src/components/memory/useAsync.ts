'use client'

import { useEffect, useState, type DependencyList } from 'react'

/**
 * A tiny discriminated-union async state, mirroring the Vite app's
 * `@/components/admin/useAsync` so the Memory panels port over with their
 * `state.status` / `state.data` / `state.message` reads unchanged.
 */
export type AsyncState<T> =
  | { status: 'loading' }
  /**
   * `message` is the sentence to render. `error` is what was actually thrown, kept
   * because a caller sometimes has to *decide* on the failure rather than show it —
   * the memory rail withholds a card on a 401/403 and keeps it on a 500, and it needs
   * the status to tell those apart.
   */
  | { status: 'error'; message: string; error: unknown }
  | { status: 'ready'; data: T }

/**
 * Run `fn` whenever `deps` change, tracking loading → ready/error. Stale results
 * from a superseded call are dropped (the classic mounted-flag guard) so a fast
 * subject switch never lands the previous subject's data.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList): { state: AsyncState<T> } {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })

  useEffect(() => {
    let alive = true
    setState({ status: 'loading' })
    fn()
      .then((data) => {
        if (alive) setState({ status: 'ready', data })
      })
      .catch((err: unknown) => {
        if (alive)
          setState({
            status: 'error',
            message:
              err instanceof Error
                ? err.message
                : 'That reading did not come back. Check the backend is running, then retry.',
            error: err,
          })
      })
    return () => {
      alive = false
    }
    // fn is recreated per render; deps drive re-fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { state }
}
