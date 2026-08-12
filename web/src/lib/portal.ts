/**
 * The role-scoped portal catalogue — a faithful port of the ROLE_SECTIONS +
 * SECTIONS structure in frontend/src/routes/Portal.tsx.
 *
 * Four portals, each a focused subset of sections scoped to what the role owns:
 *   - admin    : oversight / governance / delegation
 *   - ai_team  : builds / tunes the agent
 *   - devops   : runs the stack
 *   - client   : the tenant end-user (value, risk, read-only access)
 *
 * Every section carries a short executive label, the honest tech `hint`, a
 * plain-language `tooltip` (a hard requirement), and an optional `group` heading.
 * In this scaffold each section renders a titled placeholder page; the Console is
 * the one surface with a dedicated live-run placeholder.
 */

import {
  Brain,
  Coins,
  Cpu,
  DatabaseZap,
  GitCompareArrows,
  Inbox,
  Gauge,
  KeyRound,
  Landmark,
  Layers,
  LayoutDashboard,
  Lock,
  Network,
  PiggyBank,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Sigma,
  SlidersHorizontal,
  Sparkles,
  Swords,
  Timer,
  Waypoints,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

import type { Role } from '@/lib/stream'

export type { Role }

/** The four portal roles, in a stable order (drives the login role-select). */
export const ROLES: Role[] = ['admin', 'ai_team', 'devops', 'client']

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
  /** Whether this is the live Console surface (special placeholder). */
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
    hint: 'Qdrant',
    tooltip: 'Aegis Memory — long-term memory · Postgres + Qdrant',
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
  approvals: {
    id: 'approvals',
    label: 'Approvals',
    icon: Inbox,
    hint: 'human gate',
    tooltip: 'Aegis Tools/MCP — human gate on risky actions',
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
  admin: {
    id: 'admin',
    label: 'Settings',
    icon: SlidersHorizontal,
    hint: 'tenants · budgets',
    tooltip: 'Aegis Governance settings — tenants · budgets · usage · RBAC',
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
}

/** Which sections each role's portal exposes, in nav order (RBAC). */
export const ROLE_SECTIONS: Record<Role, string[]> = {
  admin: ['dashboard', 'governance', 'approvals', 'audit', 'roles'],
  ai_team: ['console', 'harness', 'mlops', 'llmops', 'evals', 'tokenopt', 'memory', 'rag', 'graph', 'cache', 'guardrails', 'simulation'],
  devops: ['dashboard', 'stack', 'patch', 'security', 'redteam', 'latency', 'audit'],
  client: ['dashboard', 'savings', 'risk', 'simulation'],
}

/** Section definitions for a role's portal, in nav order. */
export function sectionsFor(role: Role): Section[] {
  return ROLE_SECTIONS[role].map((id) => SECTIONS[id])
}

/** The default (first) section slug for a role. */
export function defaultSectionFor(role: Role): string {
  return ROLE_SECTIONS[role][0]
}

/**
 * The home route a role owns — its portal's default section. This is the single
 * source of truth for RBAC redirects (login lands here; a session reaching the
 * wrong portal is sent back here). Mirrors `homePathFor` in the Vite app's
 * RequireRole guard.
 */
export function homePathFor(role: Role): string {
  return `/app/${role}/${defaultSectionFor(role)}`
}

/** Whether `section` is valid for `role`. */
export function isValidSection(role: Role, section: string): boolean {
  return ROLE_SECTIONS[role]?.includes(section) ?? false
}

/** Whether `role` is one of the four portals. */
export function isRole(value: string): value is Role {
  return (ROLES as string[]).includes(value)
}

/** Human name for the portal a role owns (foot of the nav rail). */
export function portalLabelFor(role: Role): string {
  switch (role) {
    case 'admin':
      return 'Admin portal'
    case 'ai_team':
      return 'AI team portal'
    case 'devops':
      return 'DevOps portal'
    case 'client':
      return 'Client portal'
  }
}
