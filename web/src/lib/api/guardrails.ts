/**
 * The guardrail control plane (§7.6) — the effective rail stack and its provenance.
 *
 * A faithful TypeScript mirror of `GuardrailPolicyResponse` in
 * `backend/src/app/api/routes_guardrails.py`.
 *
 * **Why every control row carries three values.** A rail screen that shows what the
 * rails do without saying who decided it cannot be reasoned about: an operator reading
 * "refuses personal data" has no way to tell the platform's floor (not theirs to relax)
 * from their own tenant's tightening (theirs to review). So `value` is what the rails
 * enforce, `platform_value` is the floor, and `source` says which of the two moved it —
 * derived on the server by comparing the two policies, never by trusting a badge.
 *
 * There is no write here on purpose. Every one of these is a settings-catalogue key, so
 * the write is `PUT /settings/{key}`, which validates against the spec, refuses a
 * weakening with the resolver's own sentence and audits. A second write path for the
 * same keys would be a second policy that can disagree with the first.
 *
 * @see backend/src/app/api/routes_guardrails.py
 * @see aegis/src/aegis/guardrails/pipeline.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** What a hit on a rail does, in this configuration. */
export type RailEnforcement = 'block' | 'redact' | 'advisory' | 'off'

/** One rail in the stack, as the pipeline itself describes it. */
export interface GuardrailRail {
  id: string
  /** The verdict label the rail stamps, so a console can line a block up against it. */
  layer: string
  name: string
  screens: string
  /** `input` (which covers tool results) · `output` · `both`. */
  stage: string
  enforcement: RailEnforcement
  active: boolean
  /** Whether it needs the guardrail completer — the platform's model, never a tenant's. */
  model_backed: boolean
  threshold: string | null
  /** The catalogue keys that govern this rail, if any. */
  settings: string[]
}

/** The catalogue's own UI descriptor for a control. */
export interface GuardrailControlDescriptor {
  key: string
  type: string
  control: string
  merge: string
  description: string
  effective: boolean
  choices?: unknown[]
  minimum?: number
  maximum?: number
  inert_reason?: string
}

/** One control, its effective value, and where that value came from. */
export interface GuardrailControl {
  key: string
  value: unknown
  platform_value: unknown
  /** `platform` · `tenant` · `user`. */
  source: string
  /** For a union key, the members this tenant added on top of the floor. */
  added: unknown[] | null
  writable: boolean
  control: GuardrailControlDescriptor
}

/** Body of `GET /guardrails/policy`. */
export interface GuardrailPolicyResponse {
  tenant_id: number | null
  /** False when no tenant layer was read: the floor is the whole policy. */
  resolved: boolean
  model_layer_wired: boolean
  rails: GuardrailRail[]
  controls: GuardrailControl[]
}

/** A guardrail-policy call that kept the server's own explanation. */
export class GuardrailApiError extends ApiError {
  constructor(status: number, method: string, path: string, detail?: string) {
    super(status, method, path, detail)
    this.name = 'GuardrailApiError'
  }
}

/**
 * The rail stack this caller's tenant enforces, with each value's source.
 *
 * The tenant is the server's sealed scope and is never sent — §7.16 row 12: an
 * isolation key taken from the wire is not an isolation key.
 */
export async function getGuardrailPolicy(
  token: string | null,
): Promise<GuardrailPolicyResponse> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}/guardrails/policy`, { method: 'GET', headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new GuardrailApiError(res.status, 'GET', '/guardrails/policy', detail)
  }
  return (await res.json()) as GuardrailPolicyResponse
}
