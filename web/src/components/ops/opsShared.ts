/**
 * Shared constants + helpers for the LLMOps surface (the Aegis self-improvement
 * loop). Ported from the Vite `OpsView`; the sample prompt bodies back the diff
 * for versions the API does not expose a body for, and are badged as such.
 */

/**
 * The prompt key the loop dashboard tracks.
 *
 * Must be a key the backend can actually resolve: `/ops/prompts/active` falls
 * back to `render_floor_prompt(prompt_key)`, which looks the id up in the
 * adapter's persona table. The only real keys are `operations_lead` and
 * `client` (`adapter/personas.py`); anything else raises `KeyError` behind the
 * degrade-to-empty guard and the page silently shows nothing.
 */
export const PROMPT_KEY = 'operations_lead'

/**
 * Illustrative per-version prompt bodies used by the diff. The API only exposes
 * the *active* version's body (`/ops/prompts/active`), which we use verbatim;
 * older/proposed bodies are sample content, badged "sample" in the diff — the
 * same honest convention the rest of the surface uses for illustrative series.
 *
 * They are written against the real world: the persona is the adapter's
 * `operations_lead`, and the only tools named are the three in `TOOL_REGISTRY`
 * (`update_request_status` HIGH, `assign_request` MEDIUM, `add_case_note` LOW).
 */
export const SAMPLE_BODIES: Record<number, string> = {
  6: [
    'You are the Operations Lead: you triage, assign and resolve service requests.',
    'Ground every claim in retrieved context; if you cannot retrieve backing context, say so and stop.',
    'Before changing a request, verify the customer tier and the governing escalation policy.',
    'State which tool you will call and why it is in policy before calling it.',
    'Only call allowlisted tools. update_request_status is HIGH risk and always needs human approval.',
    'Cite the source KB article for any customer-facing statement.',
  ].join('\n'),
  4: [
    'You are the Operations Lead: you triage, assign and resolve service requests.',
    'Ground claims in retrieved context where possible.',
    'Check the customer tier before changing a request.',
    'Only call allowlisted tools. update_request_status requires human approval.',
    'Cite the source for customer-facing statements.',
  ].join('\n'),
  3: [
    'You are the Operations Lead for service requests.',
    'Use retrieved context to answer.',
    'Check the customer tier before acting.',
    'Only call allowlisted tools. Resolving a request requires approval.',
  ].join('\n'),
  2: [
    'You are the Operations Lead for service requests.',
    'Answer customer questions about their open requests.',
    'Escalate status changes to a human.',
  ].join('\n'),
  1: ['You are a support operations assistant.', 'Answer questions about service requests.'].join('\n'),
}

/** Risk level → Badge tone (low healthy, medium gate, high guardrail). */
export const RISK_TONE: Record<string, 'ok' | 'risk' | 'block'> = {
  low: 'ok',
  medium: 'risk',
  high: 'block',
}

/**
 * A compact relative-time label ("3h ago"); web has no shared datetime util.
 *
 * A missing timestamp says so. It used to return an em dash, which in a column of
 * ages reads as "just now" as easily as "never recorded" — DESIGN.md §1 wants the
 * absence stated in the slot the figure would have occupied.
 */
export function formatAgo(value: string | null): string {
  if (!value) return 'no timestamp'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return 'no timestamp'
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000))
  if (secs < 60) return `${secs}s ago`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `${days}d ago`
}
