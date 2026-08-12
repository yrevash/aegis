'use client'

import { useEffect, useState, type DependencyList } from 'react'

/**
 * A tiny discriminated-union async state, mirroring the Vite app's
 * `@/components/admin/useAsync` so the Memory panels port over with their
 * `state.status` / `state.data` / `state.message` reads unchanged.
 */
export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
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
            message: err instanceof Error ? err.message : 'Could not load. Is the backend running?',
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
