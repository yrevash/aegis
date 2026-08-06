/**
 * A tiny fetch-into-state hook shared by the admin views. Tracks loading /
 * error / ready and re-runs when `deps` change or `reload()` is called. Keeps
 * each admin surface focused on rendering rather than plumbing.
 */

import { useCallback, useEffect, useState } from 'react'

/** Async load state. */
export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: T }

/** Run `fn` on mount / when `deps` change; expose state + a manual reload. */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: readonly unknown[],
): { state: AsyncState<T>; reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const [nonce, setNonce] = useState(0)

  // `fn` is intentionally not a dep — callers pass a fresh closure each render;
  // `deps` (its real inputs) plus `nonce` drive re-runs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps)

  useEffect(() => {
    let alive = true
    setState({ status: 'loading' })
    run()
      .then((data) => {
        if (alive) setState({ status: 'ready', data })
      })
      .catch((error: unknown) => {
        if (alive) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : 'Request failed',
          })
        }
      })
    return () => {
      alive = false
    }
  }, [run, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { state, reload }
}
