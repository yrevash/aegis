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
  AreaChart,
  Brain,
  Coins,
  Cpu,
  Database,
  DatabaseZap,
  FileStack,
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
  /**
   * One plain-language line saying what the section is — **12 words at most**,
   * asserted by `web/tests/design/navTooltipLength.test.mjs`.
   *
   * It is no longer a `title=` attribute on the nav row. Thirty-four of these had
   * grown to a 28.5-word mean and a 129-word worst case inside a native browser
   * tooltip, which clips, times out and takes no keyboard focus — so the text was
   * unreadable by construction while still firing under the pointer every time it
   * crossed the rail. The explanation each one had accumulated belongs on the
   * destination screen's `PageHeader` `InfoTip`, where a reader is already looking;
   * anything longer than *that* belongs in `docs/`. What stays here is the gloss.
   */
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
  analytics: {
    id: 'analytics',
    label: 'Analytics',
    icon: AreaChart,
    hint: 'Apache Superset',
    tooltip: 'Superset charts inside Aegis, every row narrowed to your tenant server-side',
  },
  savings: {
    id: 'savings',
    label: 'Savings',
    icon: PiggyBank,
    hint: 'baseline vs actual',
    tooltip: 'What the workload would cost on the frontier model vs actual',
  },
  forecast: {
    id: 'forecast',
    label: 'Forecast',
    icon: TrendingUp,
    hint: 'statsforecast · conformal',
    tooltip: 'Aegis Forecast — spend projected forward with measured interval coverage · statsforecast',
  },
  mlops: {
    id: 'mlops',
    label: 'MLOps',
    icon: Sigma,
    hint: 'SHAP · conformal',
    tooltip: 'Aegis ML — model card, SHAP drivers, calibrated confidence · XGBoost/MAPIE',
  },
  tokenopt: {
    id: 'tokenopt',
    label: 'Token opt',
    icon: Coins,
    hint: 'routing · savings',
    tooltip: 'Aegis Gateway — role→model routing, fallback chains, savings vs frontier baseline',
  },
  evals: {
    id: 'evals',
    label: 'Evals',
    icon: Gauge,
    hint: 'RAGAS · DeepEval',
    tooltip: 'Aegis Evals — offline regression gate · deterministic RAGAS/DeepEval metrics, no LLM',
  },
  documents: {
    id: 'documents',
    icon: FileStack,
    label: 'Documents',
    hint: 'corpus · ingest',
    tooltip: 'Your tenant\'s corpus — upload a document, watch six ingest stages commit',
  },
  memory: {
    id: 'memory',
    label: 'Memory',
    icon: Brain,
    hint: 'Qdrant',
    tooltip: 'Aegis Memory — long-term memory · Postgres + Qdrant',
  },
  rag: {
    id: 'rag',
    label: 'RAG',
    icon: Network,
    hint: 'hybrid · rerank',
    tooltip: 'Which recall arms fired, RRF fusion, rerank and the Self-RAG loop',
  },
  cache: {
    id: 'cache',
    label: 'Cache',
    icon: DatabaseZap,
    hint: 'semantic · TTL',
    tooltip: 'The three real caches with their true method, backend, TTL and thresholds',
  },
  voice: {
    id: 'voice',
    label: 'Voice',
    icon: Mic,
    hint: 'Whisper · rails',
    tooltip: 'Speech transcribed on hosted Whisper, then screened by the full text rails',
  },
  vision: {
    id: 'vision',
    label: 'Vision',
    icon: ScanEye,
    hint: 'screen · then model',
    tooltip: 'Image screened for prompts aimed at an AI, then PII-redacted, then analysed',
  },
  guardrails: {
    id: 'guardrails',
    label: 'Guardrails',
    icon: ShieldCheck,
    hint: 'rails · verdicts',
    tooltip: 'The rail stack, its active engine, and the live verdict feed',
  },
  graph: {
    id: 'graph',
    label: 'Graph',
    icon: Waypoints,
    hint: 'entities · relations',
    tooltip: 'The typed entity graph, and the evidence subgraph a run stood on',
  },
  harness: {
    id: 'harness',
    label: 'Harness',
    icon: Cpu,
    hint: 'graph · tweak',
    tooltip: 'The agentic graph\'s knobs, bounds and one live glass-box trace record',
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
    tooltip: 'Every runtime, library and service in production — the DevOps inventory',
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
    tooltip: 'Per-node p50/p95/max from a per-process rolling window that resets on restart',
    group: 'Operations',
  },
  redteam: {
    id: 'redteam',
    label: 'Red-team',
    icon: Swords,
    hint: 'attacks · block-rate',
    tooltip: 'Offline attack battery scored against the guardrail stack — block-rate per category',
    group: 'Operations',
  },
  security: {
    id: 'security',
    label: 'Security',
    icon: Lock,
    hint: 'OWASP · posture',
    tooltip: 'Every OWASP-Agentic threat mapped to the live control holding it down',
    group: 'Operations',
  },
  compliance: {
    id: 'compliance',
    label: 'Compliance',
    icon: ScrollText,
    hint: 'frameworks · evidence',
    tooltip: 'Twelve frameworks mapped control by control to a file, route or test',
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
    tooltip: 'Aegis Substrate — durable background work, admission caps, and the cancel control',
    group: 'Governance',
  },
  governance: {
    id: 'governance',
    label: 'Governance',
    icon: Landmark,
    hint: 'tenants · budgets',
    tooltip: 'Aegis Governance — tenants, budgets, usage and RBAC, read from the ledger',
    group: 'Governance',
  },
  approvals: {
    id: 'approvals',
    label: 'Approvals',
    icon: Gavel,
    hint: 'the human gate',
    tooltip: 'Every action the agent paused on, why the gate fired, SLA remaining',
    group: 'Governance',
  },
  audit: {
    id: 'audit',
    label: 'Audit',
    icon: ScrollText,
    hint: 'Postgres audit',
    tooltip: 'Aegis Governance — append-only audit trail · Postgres RLS, with trace links',
    group: 'Governance',
  },
  mcp: {
    id: 'mcp',
    label: 'MCP',
    icon: Network,
    hint: 'external tool servers',
    tooltip: 'External MCP servers, their discovered tools, and the risk tier gating each',
    group: 'Governance',
  },
  database: {
    id: 'database',
    label: 'Database',
    icon: Database,
    hint: 'read-only · scoped',
    tooltip: 'Parameterised reads on a SELECT-only Postgres role, tenant-scoped server-side, fully audited',
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
    tooltip: 'Each way an autonomous agent goes wrong, and the control holding it',
    group: 'Governance',
  },
  settings: {
    id: 'settings',
    label: 'Settings',
    icon: SlidersHorizontal,
    hint: 'platform → tenant → you',
    tooltip: 'Per-tenant controls and the tool roster — every value names its scope',
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
 *   - `jobs` on `devops` — `GET /jobs` is `require_admin_or_ai_team`, so devops would
 *     get a 403 where the nav promised a control. Pipeline health reaches them through
 *     `stack` instead, which is already theirs.
 *   - `llmops` and `memory` were refused here until 7.7 and 7.5 landed. `llmops` is now
 *     on `tenant_admin` because the registry is keyed per tenant and a version no longer
 *     deletes the platform floor; `memory` is on `tenant_admin` and `client` because the
 *     write, the correction and the forget exist — a memory screen you cannot correct is
 *     a report, and it is not one any more.
 *   - `simulation` stays where it tells the isolation story and is not propagated: it
 *     is a demo artefact, not an operator tool. It was also on `client`, and could not
 *     work there: the screen runs the *same* query as two roles at once, and
 *     `_resolve_persona` refuses a `client` principal an operator-scoped persona — so
 *     one of the two lanes 403s every time, which is the isolation story working and
 *     the screen failing. A comparison you can only ever run half of is not a
 *     comparison, and offering the button anyway is the shape this list exists to
 *     forbid. It lives on `ai_team`, whose principal can drive both lanes.
 *
 * `analytics` is on the three portals whose principals *act* on it, and the acting is
 * real: an operator chooses the board and the window, and — where the deployment has
 * embedded dashboards — explores inside one. It is withheld from `ai_team` and `devops`
 * for the standard reason: neither builds nor decides anything from a tenant's business
 * board, and their operational surfaces (`harness`, `evals`, `latency`, `stack`) already
 * carry the measurements they act on. `client` keeps it under the record exception —
 * its own tenant's numbers are its own, and the backend narrows every row to that
 * tenant with a clause the browser cannot reach.
 */
export const ROLE_SECTIONS: Record<Portal, string[]> = {
  platform_admin: ['dashboard', 'analytics', 'approvals', 'governance', 'roles', 'forecast', 'jobs', 'audit', 'database', 'mcp', 'console', 'settings'],
  tenant_admin: ['dashboard', 'analytics', 'documents', 'approvals', 'governance', 'roles', 'forecast', 'jobs', 'audit', 'console', 'llmops', 'memory', 'settings'],
  ai_team: ['console', 'harness', 'mlops', 'llmops', 'evals', 'tokenopt', 'memory', 'rag', 'graph', 'cache', 'jobs', 'voice', 'vision', 'guardrails', 'simulation', 'settings'],
  devops: ['dashboard', 'stack', 'patch', 'security', 'compliance', 'redteam', 'cache', 'latency', 'audit', 'settings'],
  client: ['console', 'dashboard', 'documents', 'analytics', 'approvals', 'savings', 'forecast', 'risk', 'memory', 'settings'],
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

/**
 * Sections whose data is process-wide and cannot be narrowed to one tenant.
 *
 * A cache hit rate is one number over every tenant that shared the worker, and the
 * serving role's RLS attributes are a fact about the deployment, not about a tenant.
 * `require_infra_reader` therefore refuses a tenant-pinned principal outright, and it
 * is right to — there is no filter that would make the figure safe.
 *
 * The portal listed these anyway. `ai_team` mounts `cache`, and the seeded analyst is
 * tenant-pinned, so that nav item led to a 403 every single time it was clicked, with
 * a Retry button offering to do it again. That is the failure this file's own doctrine
 * forbids: a portal must not offer a control the backend guard makes impossible.
 *
 * The gate is the **tenant pin, not the role name**, because the same role can arrive
 * either way — an un-pinned `ai_team` operator is platform staff and may read these,
 * a tenant's own analyst may not. Keying on the role would take the section away from
 * the operator who is entitled to it.
 */
export const PLATFORM_ONLY_SECTIONS: ReadonlySet<string> = new Set(['cache'])

/**
 * Section definitions for a portal, in nav order.
 *
 * @param portal - The fine role whose navigation is being drawn.
 * @param tenantId - The principal's tenant pin, or `null` for platform staff. Omit it
 *   only where no principal is in hand (the static route manifest, the RBAC delegation
 *   map) — those describe the catalogue, not one person's view of it.
 */
export function sectionsFor(portal: Portal, tenantId?: number | null): Section[] {
  const ids =
    tenantId == null
      ? ROLE_SECTIONS[portal]
      : ROLE_SECTIONS[portal].filter((id) => !PLATFORM_ONLY_SECTIONS.has(id))
  return ids.map((id) => SECTIONS[id])
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
