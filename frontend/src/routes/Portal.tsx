import { Brain, GitCompareArrows, Inbox, LayoutDashboard, Loader2, ScrollText, SlidersHorizontal, Sparkles, Workflow } from 'lucide-react'
import { Suspense, lazy, useEffect, useState, type ReactElement } from 'react'

import { useAuth } from '@/auth/AuthContext'
import { MoneyShotConsole } from '@/components/console/MoneyShotConsole'
import { AppShell } from '@/components/layout/AppShell'
import type { NavItem } from '@/components/layout/Sidebar'
import type { Role } from '@/types/stream'

// The console is the default landing surface, so it ships in the main bundle.
// Every other section is route-split: `recharts` (dashboard + usage charts) and
// the governance views only load when their tab is opened, keeping the initial
// chunk small. Each is a named export, so unwrap `.then(m => …)`.
const Dashboard = lazy(() =>
  import('@/components/dashboard/Dashboard').then((m) => ({ default: m.Dashboard })),
)
const SimulationView = lazy(() =>
  import('@/components/sim/SimulationView').then((m) => ({ default: m.SimulationView })),
)
const ApprovalsInbox = lazy(() =>
  import('@/components/approvals/ApprovalsInbox').then((m) => ({ default: m.ApprovalsInbox })),
)
const AdminSettings = lazy(() =>
  import('@/components/admin/AdminSettings').then((m) => ({ default: m.AdminSettings })),
)
const AuditLog = lazy(() =>
  import('@/components/admin/AuditLog').then((m) => ({ default: m.AuditLog })),
)
const MemoryView = lazy(() =>
  import('@/components/memory/MemoryView').then((m) => ({ default: m.MemoryView })),
)
const OpsView = lazy(() =>
  import('@/components/ops/OpsView').then((m) => ({ default: m.OpsView })),
)

/** A neutral placeholder shown while a route-split section streams in. */
function SectionFallback(): ReactElement {
  return (
    <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      Loading…
    </div>
  )
}

interface Section {
  item: NavItem
  title: string
  render: (role: Role, token: string | null) => ReactElement
}

/** Section definitions per portal. Admin gets the audit surface too. */
function sectionsFor(role: Role): Section[] {
  // Nav labels + honest tech subtitles follow the §3.2 Aegis module map:
  // primary label is short and executive; the real tech is one hover away.
  const base: Section[] = [
    {
      item: {
        id: 'console',
        label: 'Console',
        icon: Sparkles,
        hint: 'LangGraph',
        tooltip: 'Aegis Router — multi-agent orchestration · LangGraph',
      },
      title: 'Console',
      render: () => <MoneyShotConsole />,
    },
    {
      item: {
        id: 'dashboard',
        label: 'Overview',
        icon: LayoutDashboard,
        hint: 'value at a glance',
        tooltip: 'Operations & value at a glance',
      },
      title: 'Overview',
      render: (r, token) => <Dashboard role={r} token={token} />,
    },
    {
      item: {
        id: 'memory',
        label: 'Memory',
        icon: Brain,
        hint: 'pgvector',
        tooltip: 'Aegis Memory — long-term memory · Postgres + pgvector',
      },
      title: 'Memory',
      render: (_r, token) => <MemoryView token={token} />,
    },
    {
      item: {
        id: 'simulation',
        label: 'Access demo',
        icon: GitCompareArrows,
        hint: 'RBAC scope',
        tooltip: 'Aegis Governance — same query, two roles · RBAC + retrieval scope',
      },
      title: 'Access demo',
      render: () => <SimulationView />,
    },
  ]
  if (role === 'admin') {
    base.push(
      {
        item: {
          id: 'ops',
          label: 'Improvement',
          icon: Workflow,
          hint: 'trace → eval → release',
          tooltip: 'Aegis Loop — self-improving prompts · trace → eval → release',
          group: 'Governance',
        },
        title: 'Improvement',
        render: (_r, token) => <OpsView token={token} />,
      },
      {
        item: {
          id: 'approvals',
          label: 'Approvals',
          icon: Inbox,
          hint: 'human gate',
          tooltip: 'Aegis Tools/MCP — human gate on risky actions',
          group: 'Governance',
        },
        title: 'Approvals',
        render: (_r, token) => <ApprovalsInbox token={token} />,
      },
      {
        item: {
          id: 'admin',
          label: 'Governance',
          icon: SlidersHorizontal,
          hint: 'tenants · budgets',
          tooltip: 'Aegis Governance — tenants · budgets · usage · RBAC',
          group: 'Governance',
        },
        title: 'Governance',
        render: (_r, token) => <AdminSettings token={token} />,
      },
      {
        item: {
          id: 'audit',
          label: 'Audit',
          icon: ScrollText,
          hint: 'Postgres audit',
          tooltip: 'Aegis Governance — append-only audit trail · Postgres (RLS), with trace links to Aegis Trace',
          group: 'Governance',
        },
        title: 'Audit',
        render: (_r, token) => <AuditLog token={token} />,
      },
    )
  }
  return base
}

/**
 * A role-scoped portal. Renders the shared chrome and switches between the
 * role's sections. `/admin` and `/app` mount this with different roles, so the
 * navigation and available surfaces differ by role (RBAC).
 */
/** True when a keystroke originates from a text field we must not hijack. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export function Portal({ role }: { role: Role }): ReactElement {
  const { session } = useAuth()
  const sections = sectionsFor(role)
  const [active, setActive] = useState(sections[0].item.id)
  const [presenting, setPresenting] = useState(false)
  const current = sections.find((s) => s.item.id === active) ?? sections[0]

  // Projector mode: `F` toggles present, `Esc` exits. Ignore keystrokes typed
  // into inputs so the query bar keeps working.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        setPresenting(false)
        return
      }
      if ((e.key === 'f' || e.key === 'F') && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (isTypingTarget(e.target)) return
        e.preventDefault()
        setPresenting((p) => !p)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <AppShell
      nav={sections.map((s) => s.item)}
      active={active}
      onSelect={setActive}
      title={current.title}
      portalLabel={role === 'admin' ? 'Admin portal' : 'User portal'}
      presenting={presenting}
      onExitPresent={() => setPresenting(false)}
    >
      {/* Keyed on the active section so switching tabs remounts the boundary
          and shows the fallback while the next route-split chunk streams in. */}
      <Suspense key={active} fallback={<SectionFallback />}>
        {/* Section cross-fade on tab switch (§2.5) — keyed remount fades the
            new surface in; neutralised under prefers-reduced-motion. */}
        <div className="animate-section">{current.render(role, session?.token ?? null)}</div>
      </Suspense>
    </AppShell>
  )
}
