'use client'

import { GitPullRequestArrow, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { TooltipProvider } from '@/components/primitives/tooltip'
import { CapabilityMap, type Capability } from '@/components/shared'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import {
  getOpsActivePrompt,
  getOpsEvals,
  getOpsParams,
  getOpsPendingReleases,
  getOpsPrompts,
} from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import type {
  OpsActivePromptResponse,
  OpsEvalRow,
  OpsPromptVersionRow,
  OpsReleaseApprovalRow,
} from '@/lib/api/ops'
import type { OpsParamsResponse } from '@/lib/api/platform'

import { DiagnosePanel } from './DiagnosePanel'
import { EvalTrend } from './EvalTrend'
import { LoopParams } from './LoopParams'
import { PromptHistory } from './PromptHistory'
import { ReleaseGate } from './ReleaseGate'
import { PROMPT_KEY } from './opsShared'

/** A minimal async-load result, mirroring the MLOps view's fetch pattern. */
interface Loaded<T> {
  data: T | null
  loading: boolean
  error: string | null
}

/**
 * Load with a live/mock-aware call, re-running whenever `key` (the bearer token)
 * or `nonce` changes, and held back until `enabled`.
 *
 * `AuthProvider` restores the persisted session in an effect that runs *after*
 * this one on a reload, so fetching immediately would send no bearer and 401;
 * `key` makes the call re-fire once the real token lands.
 */
function useLoad<T>(
  fn: () => Promise<T>,
  key: string | null,
  enabled: boolean,
  nonce = 0,
): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!enabled) return
    let alive = true
    setState((s) => ({ ...s, loading: true, error: null }))
    fn()
      .then((data) => {
        if (alive) setState({ data, loading: false, error: null })
      })
      .catch(() => {
        // Always resolve `loading` — a swallowed failure would spin forever.
        if (alive)
          setState({ data: null, loading: false, error: 'Could not load. Is the backend running?' })
      })
    return () => {
      alive = false
    }
    // `fn` is recreated per render; key/enabled/nonce are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled, nonce])
  return state
}

/**
 * LLMOps — the `aegis` self-improvement loop surface. It leads with the quality
 * trend the loop watches, shows the four loop steps as a live strip, then the
 * diagnosis (draft a fix), the tiered release gate (approve / reject / roll
 * back), the prompt version history with a diff, and the read-only loop
 * parameters the gate runs on. Every figure comes straight from the `/ops/*`
 * accessors — nothing is fabricated.
 */
function LLMOpsView(): ReactElement {
  // Live session token — a constant `null` would 401 every `/ops/*` accessor on a
  // reload and, being constant, never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [pendingNonce, setPendingNonce] = useState(0)
  const reloadPending = useCallback(() => setPendingNonce((n) => n + 1), [])

  const prompts = useLoad<{ rows: OpsPromptVersionRow[] }>(
    () => getOpsPrompts(token, PROMPT_KEY),
    token,
    hydrated,
  )
  const active = useLoad<OpsActivePromptResponse>(
    () => getOpsActivePrompt(token, PROMPT_KEY),
    token,
    hydrated,
  )
  const evals = useLoad<{ rows: OpsEvalRow[] }>(
    () => getOpsEvals(token, { promptKey: PROMPT_KEY, limit: 200 }),
    token,
    hydrated,
  )
  const params = useLoad<OpsParamsResponse>(() => getOpsParams(token), token, hydrated)
  const pending = useLoad<{ rows: OpsReleaseApprovalRow[] }>(
    () => getOpsPendingReleases(token, 50),
    token,
    hydrated,
    pendingNonce,
  )

  const promptRows = prompts.data?.rows ?? []
  const evalRows = evals.data?.rows ?? []
  const pendingRows = pending.data?.rows ?? []

  // Live status of the four loop steps, derived from the loaded data.
  const draftCount = promptRows.filter((r) => r.status === 'draft' || r.status === 'staged').length
  const pendingCount = pendingRows.length
  const activeVersion = active.data?.version ?? null
  const loopSteps: Capability[] = [
    {
      name: 'Watch',
      tech: evals.data ? `${evalRows.length} scores` : 'quality scores',
      status: evals.data ? 'live' : 'idle',
    },
    {
      name: 'Diagnose',
      tech: draftCount > 0 ? `${draftCount} draft${draftCount === 1 ? '' : 's'} open` : 'propose a prompt',
      status: draftCount > 0 ? 'pending' : 'idle',
    },
    {
      name: 'Gate',
      tech: pendingCount > 0 ? `${pendingCount} awaiting sign-off` : 'human approval',
      status: pendingCount > 0 ? 'pending' : 'idle',
    },
    {
      name: 'Rollback',
      tech: activeVersion != null ? `live v${activeVersion}` : 'last-good',
      status: 'idle',
    },
  ]

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">trace → eval → diagnose → release</p>
        <h1 className="t-hero text-foreground">LLMOps</h1>
      </div>

      {/* Row 1 — quality trend hero + the live loop strip. */}
      <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
        <EvalTrend rows={evalRows} loading={evals.loading} error={evals.error} />
        <Card>
          <CardHeader
            eyebrow="closed · human-gated"
            title="Loop"
            actions={
              <span className="grid size-8 place-items-center rounded-lg bg-ml/12">
                <GitPullRequestArrow className="size-4 text-ml-ink" />
              </span>
            }
          />
          <CardBody>
            <CapabilityMap items={loopSteps} />
          </CardBody>
        </Card>
      </div>

      {/* Row 2 — release gate + diagnosis. */}
      <div className="grid gap-6 lg:grid-cols-2">
        <ReleaseGate
          rows={pendingRows}
          loading={pending.loading}
          error={pending.error}
          onChanged={reloadPending}
        />
        <DiagnosePanel onChanged={reloadPending} />
      </div>

      {/* Row 3 — prompt history: versions + diff. */}
      <PromptHistory
        rows={promptRows}
        active={active.data}
        loading={prompts.loading || active.loading}
        error={prompts.error ?? active.error}
      />

      {/* Row 4 — the read-only loop parameters. */}
      <LoopParams params={params.data} loading={params.loading} error={params.error} />
    </div>
  )
}

/**
 * Client entry for the LLMOps section. Runs the boot probe once (live-first,
 * mock fallback) before mounting the view, so every `/ops/*` fetch reads the
 * resolved mode; the offline demo seeds from the mock fixtures and is labelled
 * with the honest banner — mirrors `MLOpsMount` / `ConsoleMount`.
 */
export function LLMOpsMount(): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <LLMOpsView />
    </TooltipProvider>
  )
}
