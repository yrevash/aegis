'use client'

/**
 * Polls `GET /metrics` on an interval so the efficiency dashboard stays live.
 * In mock mode the fixture drifts slightly each call, giving the numbers a
 * heartbeat without a backend.
 *
 * The returned {@link MetricsResponse} passes through the full payload verbatim,
 * so the additive `cost_saved_usd` / `baseline_cost_usd` figures (the headline
 * efficiency win) are available to consumers with no extra plumbing.
 */

import { useEffect, useState } from 'react'

import { getMetrics } from '@/lib/api/client'
import type { MetricsResponse } from '@/lib/api/types'

/** Fetch metrics now and every `intervalMs`, scoped by auth token. */
export function useMetrics(token: string | null, intervalMs = 4000): MetricsResponse | null {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)

  useEffect(() => {
    let alive = true
    const tick = (): void => {
      void getMetrics(token)
        .then((m) => {
          if (alive) setMetrics(m)
        })
        .catch(() => {
          /* transient; keep the last good value */
        })
    }
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [token, intervalMs])

  return metrics
}

/** The latest metrics plus a rolling window of the real polled samples. */
export interface MetricsSeries {
  latest: MetricsResponse | null
  /** Chronological samples, oldest → newest, capped at the window size. */
  history: MetricsResponse[]
}

/**
 * Like {@link useMetrics}, but also retains a bounded history of the samples it
 * has polled so the dashboard can draw a *measured* trend line. The history is
 * genuine `GET /metrics` data accumulated over the session — nothing synthetic.
 */
export function useMetricsSeries(
  token: string | null,
  intervalMs = 4000,
  window = 24,
): MetricsSeries {
  const [series, setSeries] = useState<MetricsSeries>({ latest: null, history: [] })

  useEffect(() => {
    let alive = true
    // A token change scopes to a different principal — start its history fresh.
    setSeries({ latest: null, history: [] })
    const tick = (): void => {
      void getMetrics(token)
        .then((m) => {
          if (!alive) return
          setSeries((prev) => ({ latest: m, history: [...prev.history, m].slice(-window) }))
        })
        .catch(() => {
          /* transient; keep the last good value + history */
        })
    }
    tick()
    const id = setInterval(tick, intervalMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [token, intervalMs, window])

  return series
}