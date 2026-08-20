/**
 * Skills — authoring a `SKILL.md`, and putting one in force (§10.1–10.3).
 *
 * A faithful TypeScript mirror of `SkillRow` / `SkillWriteResponse` in
 * `backend/src/app/api/routes_skills.py`.
 *
 * **Why this module has its own fetch rather than `client.ts`'s `request`.** Same
 * reason as `seats.ts`: that helper discards the response body, and here the body is
 * the point. An authored skill is refused with a 422 whose sentence is the *guardrail's
 * own* — "the guardrails refused this skill's body, so nothing was stored: …" — and
 * collapsing that into "request failed" would turn the one security decision this
 * screen exists to make visible into a shrug.
 *
 * **A skill is screened when it is written, not when it is used.** That is why the
 * write response carries `verdict` and `redactions`: a REDACT verdict stores the
 * *redacted* text, so the author has to be told which kinds were masked rather than
 * finding `[REDACTED_PERSON]` in their own runbook a week later.
 *
 * @see backend/src/app/api/routes_skills.py
 * @see aegis/src/aegis/skills/store.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** The three layers a skill can live at, strongest first. */
export type SkillScope = 'platform' | 'tenant' | 'user'

/** One authored skill, with the layer it lives at and whether it is live. */
export interface Skill {
  name: string
  scope: SkillScope
  description: string
  /** The whole skill as a SKILL.md document — what the editor loads. */
  document: string
  triggers: string[]
  /** Whether it resolves for a run right now. Not the same as "the row exists". */
  inForce: boolean
  /** A platform safety skill: no other layer may rebind its name. */
  isSafety: boolean
  updatedBy: string | null
}

/** Body of `GET /skills`. */
export interface SkillsResponse {
  rows: Skill[]
  /** The layers this caller may author at. A hint; the server still refuses. */
  scopes: SkillScope[]
}

/** What `POST /skills` returns: the row, and what the rail did to it on the way in. */
export interface SkillWriteResult {
  row: Skill
  /** pass | flag | redact — the input rail's verdict. A block never gets here; it 422s. */
  verdict: string
  /** PII kinds masked before storage. Non-empty means the stored text is not the typed text. */
  redactions: string[]
}

async function call<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new ApiError(res.status, method, path, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Every skill this caller can see, at every layer they can see it at. */
export async function getSkills(token: string | null): Promise<SkillsResponse> {
  return call<SkillsResponse>('/skills', { method: 'GET' }, token)
}

/**
 * Author one skill from a SKILL.md document.
 *
 * Neither the tenant nor the user is on the wire: both are the server's to decide from
 * the sealed scope, and a body that could name either is a cross-tenant write waiting
 * to happen.
 */
export async function authorSkill(
  token: string | null,
  body: { document: string; scope: SkillScope; isSafety?: boolean; enable?: boolean },
): Promise<SkillWriteResult> {
  return call<SkillWriteResult>(
    '/skills',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** Put one skill in force at a layer, or take it out. */
export async function setSkillActive(
  token: string | null,
  scope: SkillScope,
  name: string,
  active: boolean,
): Promise<Skill> {
  return call<Skill>(
    `/skills/${scope}/${encodeURIComponent(name)}/active?active=${active}`,
    { method: 'PUT' },
    token,
  )
}

/** Delete one authored skill and take its name out of force. */
export async function deleteSkill(
  token: string | null,
  scope: SkillScope,
  name: string,
): Promise<void> {
  await call<void>(
    `/skills/${scope}/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
    token,
  )
}
