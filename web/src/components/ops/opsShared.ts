/**
 * Shared constants + helpers for the LLMOps surface (the Aegis self-improvement
 * loop). Ported from the Vite `OpsView`; the sample prompt bodies back the diff
 * for versions the API does not expose a body for, and are badged as such.
 */

/** The prompt key the loop dashboard tracks (the console scenario's agent). */
export const PROMPT_KEY = 'payments_ops_agent'

/**
 * Illustrative per-version prompt bodies used by the diff. The API only exposes
 * the *active* version's body (`/ops/prompts/active`), which we use verbatim;
 * older/proposed bodies are sample content, badged "sample" in the diff — the
 * same honest convention the rest of the surface uses for illustrative series.
 */
export const SAMPLE_BODIES: Record<number, string> = {
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

/** Risk level → Badge tone (low healthy, medium gate, high guardrail). */
export const RISK_TONE: Record<string, 'ok' | 'risk' | 'block'> = {
  low: 'ok',
  medium: 'risk',
  high: 'block',
}

/** A compact relative-time label ("3h ago"); web has no shared datetime util. */
export function formatAgo(value: string | null): string {
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
