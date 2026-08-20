'use client'

import { useEffect, useState } from 'react'

import { getCapabilities } from '@/lib/api/client'
import type { CapabilitiesResponse } from '@/lib/api/types'

/**
 * The public capability manifest, fetched once for the whole page.
 *
 * Two sections read it — the hero, which says whether this page is talking to a
 * running backend at all, and the platform section, which lists the modules. A
 * hook per component would hit the endpoint twice on every load for one
 * unchanging document, and worse, would let the two disagree for a frame: the
 * hero could say "connected" while the grid was still empty.
 *
 * The promise is cached at module scope rather than in a context, because there
 * is exactly one manifest per page load and it never invalidates. A failed fetch
 * is cached too — a backend that is down stays down for the life of the page, and
 * retrying on every mount would turn an unreachable API into a request storm.
 */

/** What the page knows about the backend right now. */
export type Manifest =
  | { state: 'loading' }
  | { state: 'up'; data: CapabilitiesResponse }
  | { state: 'down' }

let inflight: Promise<Manifest> | null = null

function load(): Promise<Manifest> {
  inflight ??= getCapabilities()
    .then((data): Manifest => ({ state: 'up', data }))
    .catch((): Manifest => ({ state: 'down' }))
  return inflight
}

/** Read the manifest. Renders `loading` on the server and the first client frame. */
export function useCapabilities(): Manifest {
  const [manifest, setManifest] = useState<Manifest>({ state: 'loading' })

  useEffect(() => {
    let live = true
    void load().then((result) => {
      if (live) setManifest(result)
    })
    return () => {
      live = false
    }
  }, [])

  return manifest
}
