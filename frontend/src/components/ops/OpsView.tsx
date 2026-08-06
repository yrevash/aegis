import { GitPullRequestArrow } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { getOpsActivePrompt, getOpsEvals, getOpsPendingReleases, getOpsPrompts } from '@/api/client'
import { useAsync } from '@/components/admin/useAsync'
import { BentoGrid, BentoTile, CapabilityMap, type Capability } from '@/components/shared'
import { InfoTip } from '@/components/ui/InfoTip'

import { DiagnosePanel } from './DiagnosePanel'
import { EvalTrend } from './EvalTrend'
import { PendingReleases } from './PendingReleases'
import { PromptDiff, type DiffSide } from './PromptDiff'
import { PromptTimeline } from './PromptTimeline'

const PROMPT_KEY = 'payments_ops_agent'

/**
 * Illustrative per-version prompt bodies used by the diff. The API only exposes
 * the *active* version's body (`/ops/prompts/active`), which we use verbatim;
 * older/proposed bodies are sample content, badged as such in the diff — the same
 * honest convention the dashboard uses for its illustrative series.
 */
const SAMPLE_BODIES: Record<number, string> = {
  6: [
    'You are the Payments Operations agent.',
    'Ground every claim in retrieved context; if you cannot retrieve backing context, say so and stop.',
    'For any refund or cancellation, verify the entitlement tier and the governing Refund Policy before acting.',
    'Before issuing a refund, explicitly check the amount against the $2,000 ceiling and state the result.',
    'Only call allowlisted tools. Never issue a refund above $2,000 without human approval.',
    'Cite the source AND the Refund Policy version for any customer-facing statement.',
  ].join('\n'),
  4: [
    'You are the Payments Operations agent.',
    'Ground claims in retrieved context where possible.',
    'For refunds, check the entitlement tier before acting.',
    'Only call allowlisted tools. Refunds above $2,000 require human approval.',
    'Cite the source for customer-facing statements.',
  ].join('\n'),
  3: [
    'You are the Payments Operations agent.',
    'Use retrieved context to answer.',
    'For refunds, check the entitlement tier.',
    'Only call allowlisted tools. Large refunds require approval.',
  ].join('\n'),
  2: [
    'You are the Payments Operations agent.',
    'Answer customer billing questions.',
    'Escalate refunds to a human.',
  ].join('\n'),
  1: ['You are a payments support assistant.', 'Answer billing questions.'].join('\n'),
}

/**
 * The **Improvement** surface (Aegis Loop, §4.4) — the closed loop that lets the
 * system improve its own prompts under a human gate. It leads with the quality
 * trend, shows the four loop steps as a live capability strip, the release gate,
 * the diagnosis, and the prompt version history with a diff.
 */
export function OpsView({ token }: { token: string | null }): ReactElement {
  const prompts = useAsync(() => getOpsPrompts(token, PROMPT_KEY), [token])
  const active = useAsync(() => getOpsActivePrompt(token, PROMPT_KEY), [token])
  const evals = useAsync(() => getOpsEvals(token, { promptKey: PROMPT_KEY, limit: 200 }), [token])
  const [pendingNonce, setPendingNonce] = useState(0)
  const pending = useAsync(() => getOpsPendingReleases(token, 50), [token, pendingNonce])

  const rows = prompts.state.status === 'ready' ? prompts.state.data.rows : []
  const activeVersion = active.state.status === 'ready' ? active.state.data.version : null
  const stagedVersion = rows.find((r) => r.status === 'staged')?.version ?? null

  // Default the diff to compare the active version against the staged proposal.
  const [base, setBase] = useState<number | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const effBase = base ?? activeVersion
  const effTarget = target ?? stagedVersion

  const bodyFor = useMemo(
    () =>
      (version: number | null): DiffSide | null => {
        if (version == null) return null
        if (version === activeVersion && active.state.status === 'ready') {
          return { version, text: active.state.data.system_prompt, sample: false }
        }
        const text = SAMPLE_BODIES[version] ?? null
        return { version, text, sample: text != null }
      },
    [activeVersion, active.state],
  )

  // Tap a version to fill the diff pair (base first, then target; tap again clears).
  const selectVersion = (v: number): void => {
    if (v === effBase) {
      setBase(effTarget ?? null)
      setTarget(null)
      return
    }
    if (v === effTarget) {
      setTarget(null)
      return
    }
    if (effBase == null) setBase(v)
    else setTarget(v)
  }

  const reloadPending = (): void => setPendingNonce((n) => n + 1)

  // Live status of the four loop steps, derived from the loaded data.
  const draftCount = rows.filter((r) => r.status === 'draft' || r.status === 'staged').length
  const pendingCount = pending.state.status === 'ready' ? pending.state.data.rows.length : 0
  const loopSteps: Capability[] = [
    {
      name: 'Watch',
      tech: evals.state.status === 'ready' ? 'scoring runs' : 'quality scores',
      status: evals.state.status === 'ready' ? 'live' : 'idle',
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
    { name: 'Rollback', tech: 'one click, last-good', status: 'idle' },
  ]

  return (
    <BentoGrid className="gap-4">
      {/* Row 1 — quality-trend hero + the live loop strip. */}
      <BentoTile span={8} rows={2} hero reveal index={0}>
        <EvalTrend state={evals.state} />
      </BentoTile>
      <BentoTile span={4} reveal index={1}>
        <div className="mb-4 flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-lg bg-ml/12">
            <GitPullRequestArrow className="size-4 text-ml-ink" />
          </span>
          <h3 className="t-title text-foreground">Loop</h3>
          <InfoTip label="About the loop">
            A closed, human-gated loop: watch quality, diagnose failures, gate risky changes, and
            roll back on demand.
          </InfoTip>
        </div>
        <CapabilityMap items={loopSteps} />
      </BentoTile>

      {/* Row 2 — release gate + diagnosis. */}
      <BentoTile span={5} reveal index={2}>
        <PendingReleases state={pending.state} token={token} promptKey={PROMPT_KEY} onChanged={reloadPending} />
      </BentoTile>
      <BentoTile span={7} reveal index={3}>
        <DiagnosePanel token={token} promptKey={PROMPT_KEY} onChanged={reloadPending} />
      </BentoTile>

      {/* Row 3 — prompt history: versions + diff. */}
      <BentoTile span={4} reveal index={4}>
        <PromptTimeline state={prompts.state} base={effBase} target={effTarget} onSelect={selectVersion} />
      </BentoTile>
      <BentoTile span={8} reveal index={5}>
        <PromptDiff base={bodyFor(effBase)} target={bodyFor(effTarget)} />
      </BentoTile>
    </BentoGrid>
  )
}
