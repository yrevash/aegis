/**
 * The portal catalogue — which sections each portal exposes, in nav order.
 *
 * **Five portals, keyed on the fine role** (§7.2). They used to be four, keyed on the
 * coarse one, and the missing fifth was the whole defect: `admin` collapses the Aegis
 * operator and a customer's own administrator into one value, so the two landed on the
 * same URL, the same nav and the same screens. A tenant admin had no portal at all —
 * it was borrowing the platform's. `fine_role` reaches the browser on
 * `POST /auth/login`; this module is what the browser does with it.
 *
 *   - platform_admin : operates Aegis itself — every tenant, no tenant pin
 *   - tenant_admin   : administers exactly one tenant
 *   - ai_team        : builds / tunes the agent
 *   - devops         : runs the stack
 *   - client         : the tenant end-user (value, risk, their own governance)
 *
 * **The rule for adding a section to a portal: that role must be able to *act* on it.**
 * A read-only copy of someone else's screen is a tab on their screen, not a section on
 * this one — it is a gap wearing a menu entry, and it reads as capability the operator
 * does not have. The one deliberate exception is a role's own record: a tenant admin's
 * audit trail and a client's own approvals are theirs to read even where the write
 * belongs elsewhere.
 *
 * Every section carries a short executive label, the honest tech `hint`, a
 * plain-language `tooltip` (a hard requirement), and an optional `group` heading.
 * Every section listed in ROLE_SECTIONS renders a live surface — asserted from the
 * backend by `backend/tests/api/test_route_coverage.py`, which reads this file.
 */

import {
  Brain,
  Coins,
  Cpu,
  DatabaseZap,
  GitCompareArrows,
  Gauge,
  Gavel,
  KeyRound,
  Landmark,
  ListChecks,
  Layers,
  LayoutDashboard,
  Lock,
  Mic,
  Network,
  ScanEye,
  PiggyBank,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Sigma,
  SlidersHorizontal,
  Sparkles,
  Swords,
  Timer,
  TrendingUp,
  Waypoints,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

import type { FineRole } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

export type { Role }

/**
 * A portal — the fine RBAC tier, which is the granularity a navigation must have.
 *
 * Deliberately the *same* type as `FineRole` rather than a parallel union: the portal
 * a session may enter is the tier the backend issued it, and a second list of names
 * here could disagree with the JWT while still type-checking.
 */
export type Portal = FineRole

/** The five portals, in a stable order. */
export const PORTALS: Portal[] = [
  'platform_admin',
  'tenant_admin',
  'ai_team',
  'devops',
  'client',
]

/** A navigation entry / section definition. */
export interface Section {
  /** Stable id — also the URL slug under /app/[role]/[section]. */
  id: string
  label: string
  icon: LucideIcon
  /** Short mono caption — the honest tech, kept terse. */
  hint: string
  /** Full plain-language subtitle surfaced on hover / in the header. */
  tooltip: string
  /** Optional group heading this item sits under (defaults to "Workspace"). */
  group?: string
  /** Whether this is the live Console surface. */
  console?: boolean
}

/** The full section catalogue, keyed by id (mirrors SECTIONS in Portal.tsx). */
export const SECTIONS: Record<string, Section> = {
  console: {
    id: 'console',
    label: 'Console',
    icon: Sparkles,
    hint: 'LangGraph',
    tooltip: 'Aegis Router — multi-agent orchestration · LangGraph',
    console: true,
  },
  dashboard: {
    id: 'dashboard',
    label: 'Overview',
    icon: LayoutDashboard,
    hint: 'value at a glance',
    tooltip: 'Operations & value at a glance',
  },
  savings: {
    id: 'savings',
    label: 'Savings',
    icon: PiggyBank,
    hint: 'baseline vs actual',
    tooltip: 'What the workload would cost on the frontier model vs what it actually cost',
  },
  forecast: {
    id: 'forecast',
    label: 'Forecast',
    icon: TrendingUp,
    hint: 'statsforecast · conformal',
    tooltip: 'Aegis Forecast — spend and demand projected forward, with the interval coverage that was actually measured · statsforecast',
  },
  mlops: {
    id: 'mlops',
    label: 'MLOps',
    icon: Sigma,
    hint: 'SHAP · conformal',
    tooltip: 'Aegis ML — model card, SHAP drivers & calibrated confidence · XGBoost + MAPIE',
  },
  tokenopt: {
    id: 'tokenopt',
    label: 'Token opt',
    icon: Coins,
    hint: 'routing · savings',
    tooltip:
      'Aegis Gateway — role→model routing, fallback chains, and savings vs the frontier baseline · metered from live calls',
  },
  evals: {
    id: 'evals',
    label: 'Evals',
    icon: Gauge,
    hint: 'RAGAS · DeepEval',
    tooltip:
      'Aegis Evals — the offline regression gate: deterministic RAGAS/DeepEval-pattern metrics scored with no LLM',
  },
  memory: {
    id: 'memory',
    label: 'Memory',
    icon: Brain,
    hint: 'embedded Chroma',
    tooltip: 'Aegis Memory — long-term memory · Postgres + embedded Chroma',
  },
  rag: {
    id: 'rag',
    label: 'RAG',
    icon: Network,
    hint: 'hybrid · rerank',
    tooltip:
      'Aegis Retrieval — the arsenal made observable: which recall arms fired (vector/graph/bm25), RRF fusion, LLM rerank, spotlighting, query-rewrite and the bounded Self-RAG loop, with real measured counts',
  },
  cache: {
    id: 'cache',
    label: 'Cache',
    icon: DatabaseZap,
    hint: 'semantic · TTL',
    tooltip:
      'Aegis Caches — the three real caches (memory recall, retrieval + answers, guardrail verdicts) with their true method, backend, TTL & thresholds · RedisVL / Redis / in-memory',
  },
  voice: {
    id: 'voice',
    label: 'Voice',
    icon: Mic,
    hint: 'Whisper · rails',
    tooltip:
      'Aegis Voice — record or upload speech, transcribed on the fleet\'s hosted Whisper and split on silence for long audio; the transcript is then screened by the FULL text rail stack before a word of it can reach the agent',
  },
  vision: {
    id: 'vision',
    label: 'Vision',
    icon: ScanEye,
    hint: 'screen · then model',
    tooltip:
      'Aegis Vision — upload an image and watch the ordering that makes vision safe: payload hygiene, then a cheap vision call that screens the pixels for instructions aimed at an AI, then image-PII redaction, and only then the hosted Llama-3.2-90B-Vision analysis and the platform\'s own output rails; an image that fails the screen never reaches the answering model, and if the screen cannot run the image is blocked rather than passed',
  },
  guardrails: {
    id: 'guardrails',
    label: 'Guardrails',
    icon: ShieldCheck,
    hint: 'rails · verdicts',
    tooltip:
      'Aegis Guardrails — the defense-in-depth rail stack (schema → PII → injection → content-safety → topical → grounding), the active engine (programmatic vs NeMo Colang), the live verdict feed, and a red-team block-rate teaser',
  },
  graph: {
    id: 'graph',
    label: 'Graph',
    icon: Waypoints,
    hint: 'entities · relations',
    tooltip:
      'Aegis Knowledge Graph — the typed entity graph (organizations, people, products, policies…) and the real relations between them; a run highlights the evidence subgraph the answer stood on',
  },
  harness: {
    id: 'harness',
    label: 'Harness',
    icon: Cpu,
    hint: 'graph · tweak',
    tooltip:
      'Aegis Harness — the agentic graph made legible: every tunable knob (gate, self-repair, retrieval loop, caches) with its value/default/bounds, and a live run folded into one glass-box trace record (node timeline · gate · tools · outcome)',
  },
  simulation: {
    id: 'simulation',
    label: 'Access demo',
    icon: GitCompareArrows,
    hint: 'RBAC scope',
    tooltip: 'Aegis Governance — same query, two roles · RBAC + retrieval scope',
  },
  stack: {
    id: 'stack',
    label: 'Tech Stack & Versions',
    icon: Layers,
    hint: 'SBOM',
    tooltip: 'Every runtime, library and service in production — so DevOps knows exactly what is running',
    group: 'Operations',
  },
  patch: {
    id: 'patch',
    label: 'Patch Check',
    icon: ShieldCheck,
    hint: 'installed vs latest',
    tooltip: 'Flags outdated dependencies before a known-CVE lapse — installed compared to latest',
    group: 'Operations',
  },
  latency: {
    id: 'latency',
    label: 'Latency',
    icon: Timer,
    hint: 'p50 · p95',
    tooltip:
      'Aegis Latency — per-node and per-run p50/p95/max drawn from real samples in a per-process rolling window (resets on restart); no runs yet reads as an honest empty state, never fake zeros',
    group: 'Operations',
  },
  redteam: {
    id: 'redteam',
    label: 'Red-team',
    icon: Swords,
    hint: 'attacks · block-rate',
    tooltip:
      'Aegis Red-team — the offline attack battery (prompt injection, jailbreak, system-prompt leak, PII extraction, content safety) scored against the guardrail stack: overall block-rate, gate pass/fail, per-category bars and the probes that leaked',
    group: 'Operations',
  },
  security: {
    id: 'security',
    label: 'Security',
    icon: Lock,
    hint: 'OWASP · posture',
    tooltip:
      'Aegis Security posture — every OWASP-Agentic threat mapped to the live Aegis control holding it down, with an honest enforced / partial / not-covered status derived from real wiring signals',
    group: 'Operations',
  },
  llmops: {
    id: 'llmops',
    label: 'LLMOps',
    icon: Workflow,
    hint: 'trace → eval → release',
    tooltip: 'Aegis Loop — self-improving prompts · trace → eval → release',
    group: 'Governance',
  },
  jobs: {
    id: 'jobs',
    label: 'Jobs',
    icon: ListChecks,
    hint: 'durable queue',
    tooltip:
      'Aegis Substrate — durable background work, its admission caps, and the cancel control',
    group: 'Governance',
  },
  governance: {
    id: 'governance',
    label: 'Governance',
    icon: Landmark,
    hint: 'tenants · budgets',
    tooltip: 'Aegis Governance — tenants, budgets, usage & RBAC read straight from the ledger',
    group: 'Governance',
  },
  approvals: {
    id: 'approvals',
    label: 'Approvals',
    icon: Gavel,
    hint: 'the human gate',
    tooltip:
      'Aegis Approvals — every action the agent paused on rather than took: what approving would run, why the gate fired, and how long is left on its SLA. Deciding one resumes the parked run.',
    group: 'Governance',
  },
  audit: {
    id: 'audit',
    label: 'Audit',
    icon: ScrollText,
    hint: 'Postgres audit',
    tooltip: 'Aegis Governance — append-only audit trail · Postgres (RLS), with trace links to Aegis Trace',
    group: 'Governance',
  },
  roles: {
    id: 'roles',
    label: 'Roles & Access',
    icon: KeyRound,
    hint: 'RBAC grants',
    tooltip: 'Who can reach which portal — the front line of least-privilege access',
    group: 'Governance',
  },
  risk: {
    id: 'risk',
    label: 'Risk Map',
    icon: ShieldAlert,
    hint: 'OWASP-Agentic',
    tooltip: 'How an autonomous agent can go wrong — and the control holding each risk down',
    group: 'Governance',
  },
  settings: {
    id: 'settings',
    label: 'Settings',
    icon: SlidersHorizontal,
    hint: 'platform → tenant → you',
    tooltip: 'The per-tenant control catalogue and the tool roster — every value shows which scope decided it',
    group: 'Governance',
  },
}

/**
 * Which sections each portal exposes, in nav order (RBAC).
 *
 * `console` leads the client portal: the client is the role the product exists
 * for, and without it the tenant end-user has every read-only report and no way
 * to ask a question. `POST /query` has always admitted every authenticated role
 * (`require_auth`); only this catalogue withheld the surface. The route-coverage
 * test in `backend/tests/api/test_route_coverage.py` is what stops it being
 * dropped again.
 *
 * **What each portal is for, and what was deliberately withheld from it.**
 *
 * `platform_admin` operates Aegis. It keeps every section the old `admin` portal had
 * and gains `approvals`, where it decides the gates Aegis's own runs raised and *sees*
 * — without deciding — every tenant's. `tenant_admin` is the portal that did not
 * exist: the tenant's own administrator, whose sections are the ones the backend lets
 * a `tenant_admin` write (`require_tenant_admin` pins each to its own tenant) plus its
 * own tenant's record. `client` gains `approvals` too, read-only: a user whose run
 * tripped the gate had no screen that told them what became of it.
 *
 * `console` is on both admin portals, and on the platform operator's it is load-bearing
 * rather than a courtesy: the gates a platform admin may decide are the ones carrying no
 * tenant, and an un-tenanted run is the only thing that raises one. Without a console
 * the operator's own approvals queue could only ever be filled by somebody else.
 *
 * Refused on purpose, and each is a task that owns it rather than an oversight:
 *
 *   - `llmops` on `tenant_admin` — the prompt-registry read path is not yet
 *     per-tenant (`_ACTIVE_CACHE` is keyed on the prompt key alone), so the screen
 *     would look right and show another tenant's active version. Task 7.7 fixes the
 *     read path *before* mounting the surface.
 *   - `memory` on `tenant_admin` / `client` — read-only today; the write and the
 *     forget are task 7.5. A memory screen you cannot correct is a report.
 *   - `jobs` on `devops` — `GET /jobs` is `require_admin_or_ai_team`, so devops would
 *     get a 403 where the nav promised a control.
 *   - `simulation` stays where it tells the isolation story and is not propagated: it
 *     is a demo artefact, not an operator tool.
 */
export const ROLE_SECTIONS: Record<Portal, string[]> = {
  platform_admin: ['dashboard', 'approvals', 'governance', 'roles', 'forecast', 'jobs', 'audit', 'console', 'settings'],
  tenant_admin: ['dashboard', 'approvals', 'governance', 'roles', 'forecast', 'jobs', 'audit', 'console', 'settings'],
  ai_team: ['console', 'harness', 'mlops', 'llmops', 'evals', 'tokenopt', 'memory', 'rag', 'graph', 'cache', 'jobs', 'voice', 'vision', 'guardrails', 'simulation', 'settings'],
  devops: ['dashboard', 'stack', 'patch', 'security', 'redteam', 'latency', 'audit', 'settings'],
  client: ['console', 'dashboard', 'approvals', 'savings', 'forecast', 'risk', 'simulation', 'settings'],
}

/**
 * The coarse data-layer role a portal's principal holds.
 *
 * Both admin tiers are `admin` to the backend's four-valued `Role`, which is what
 * several surfaces (the console, the dashboards, the forecast) still take — they
 * branch on what the *data* is scoped by, not on which portal is showing it. This is
 * the one place the widening happens, so a component never has to guess.
 */
export function coarseRoleFor(portal: Portal): Role {
  switch (portal) {
    case 'platform_admin':
    case 'tenant_admin':
      return 'admin'
    default:
      return portal
  }
}

/** Section definitions for a portal, in nav order. */
export function sectionsFor(portal: Portal): Section[] {
  return ROLE_SECTIONS[portal].map((id) => SECTIONS[id])
}

/** The default (first) section slug for a portal. */
export function defaultSectionFor(portal: Portal): string {
  return ROLE_SECTIONS[portal][0]
}

/**
 * The home route a portal owns — its default section. This is the single source of
 * truth for RBAC redirects (login lands here; a session reaching the wrong portal is
 * sent back here).
 */
export function homePathFor(portal: Portal): string {
  return `/app/${portal}/${defaultSectionFor(portal)}`
}

/** Whether `section` is valid for `portal`. */
export function isValidSection(portal: Portal, section: string): boolean {
  return ROLE_SECTIONS[portal]?.includes(section) ?? false
}

/** Whether `value` names one of the five portals. */
export function isPortal(value: string): value is Portal {
  return (PORTALS as string[]).includes(value)
}

/** Human name for the portal (foot of the nav rail). */
export function portalLabelFor(portal: Portal): string {
  switch (portal) {
    case 'platform_admin':
      return 'Platform admin portal'
    case 'tenant_admin':
      return 'Tenant admin portal'
    case 'ai_team':
      return 'AI team portal'
    case 'devops':
      return 'DevOps portal'
    case 'client':
      return 'Client portal'
  }
}
