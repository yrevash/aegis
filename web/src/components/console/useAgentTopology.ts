'use client'

/**
 * The real compiled graph, fetched once.
 *
 * Extracted from {@link OrchestrationMap} so the flow tab and the compact map read the
 * same topology from the same place rather than each keeping their own copy of the
 * fallback rule.
 */

import { useEffect, useState } from 'react'

import { getAgentTopology } from '@/lib/api/client'
import type { AgentTopologyResponse } from '@/lib/api/types'

import { FALLBACK_TOPOLOGY } from './orchestration'

/**
 * Fetch the real graph topology once, falling back to the generated snapshot.
 *
 * The fallback is the initial value, so the map paints immediately and a failed or
 * unauthenticated fetch simply leaves the correct offline picture in place — the
 * snapshot is generated from `aegis.agent.graph_topology()` and a backend test fails if
 * it stops matching the live graph, so "never blank" here does not cost "never wrong".
 */
export function useAgentTopology(): AgentTopologyResponse {
  const [topology, setTopology] = useState<AgentTopologyResponse>(FALLBACK_TOPOLOGY)
  useEffect(() => {
    let live = true
    getAgentTopology(null)
      .then((served) => {
        if (live && served.nodes.length > 0) setTopology(served)
      })
      .catch(() => {
        /* backend unreachable — the snapshot fallback already renders. */
      })
    return () => {
      live = false
    }
  }, [])
  return topology
}
