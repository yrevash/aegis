import { notFound } from 'next/navigation'
import { AuditMount } from '@/components/admin/AuditLog'
import { AnalyticsMount } from '@/components/analytics/AnalyticsView'
import { ApprovalsMount } from '@/components/approval/ApprovalInbox'
import { RolesAccessMount } from '@/components/admin/RolesAccess'
import { CacheMount } from '@/components/cache/CacheView'
import { ComplianceMount } from '@/components/compliance/ComplianceView'
import { RiskMount } from '@/components/client/RiskMap'
import { DatabaseMount } from '@/components/db/DatabaseView'
import { DocumentsMount } from '@/components/documents/DocumentsView'
import { SavingsMount } from '@/components/client/SavingsView'
import { ConsoleMount } from '@/components/console/ConsoleMount'
import { AdminDashboardMount } from '@/components/dashboard/AdminCommandCenter'
import { DashboardMount } from '@/components/dashboard/Dashboard'
import { PatchMount } from '@/components/devops/PatchCheck'
import { StackMount } from '@/components/devops/StackVersions'
import { EvalsMount } from '@/components/evals/EvalsView'
import { ForecastMount } from '@/components/forecast/ForecastView'
import { TokenOptMount } from '@/components/gateway/TokenOptView'
import { GovernanceMount } from '@/components/governance/GovernanceView'
import { GraphMount } from '@/components/graph/GraphView'
import { GuardrailsMount } from '@/components/guardrail/GuardrailsView'
import { InteropMount } from '@/components/interop/InteropView'
import { JobsMount } from '@/components/jobs/JobsView'
import { HarnessMount } from '@/components/harness/HarnessView'
import { LatencyMount } from '@/components/latency/LatencyView'
import { McpConsoleMount } from '@/components/mcp/McpConsoleView'
import { MLOpsMount } from '@/components/ml/MLOpsView'
import { MemoryControlMount } from '@/components/memoryctl/MemoryControlView'
import { LLMOpsMount } from '@/components/ops/LLMOpsView'
import { RagMount } from '@/components/retrieval/RagView'
import { RedteamMount } from '@/components/redteam/RedteamView'
import { SecurityMount } from '@/components/security/SecurityView'
import { SimulationMount } from '@/components/sim/SimulationView'
import { VisionMount } from '@/components/vision/VisionView'
import { VoiceMount } from '@/components/voice/VoiceView'
import { coarseRoleFor, isPortal, isValidSection, ROLE_SECTIONS, SECTIONS } from '@/lib/portal'

import { SettingsMount } from './SettingsView'

/**
 * A single portal section. Validates that `section` is exposed by `portal` (RBAC),
 * then renders that section's live surface — every section in ROLE_SECTIONS has
 * one, so an unmatched slug is a 404 rather than an empty shell.
 * generateStaticParams enumerates every portal/section combo so `next build`
 * prints the full portal route tree.
 *
 * The `[role]` segment carries the **fine** role — the portal (§7.2). Components that
 * still take the coarse four-valued `Role` are handed `coarseRoleFor(portal)`: they
 * branch on what their data is scoped by, not on which portal is showing it.
 */
export function generateStaticParams(): Array<{ role: string; section: string }> {
  return Object.entries(ROLE_SECTIONS).flatMap(([portal, sections]) =>
    sections.map((section) => ({ role: portal, section })),
  )
}

export default async function SectionPage({
  params,
}: {
  params: Promise<{ role: string; section: string }>
}) {
  const { role: portal, section } = await params
  if (!isPortal(portal) || !isValidSection(portal, section)) notFound()

  const role = coarseRoleFor(portal)
  const def = SECTIONS[section]
  if (def.console) return <ConsoleMount role={role} />
  if (section === 'analytics') return <AnalyticsMount />
  if (section === 'approvals') return <ApprovalsMount />
  if (section === 'harness') return <HarnessMount role={role} />
  if (section === 'mlops') return <MLOpsMount />
  if (section === 'llmops') return <LLMOpsMount />
  if (section === 'evals') return <EvalsMount />
  if (section === 'forecast') return <ForecastMount role={role} />
  if (section === 'documents') return <DocumentsMount />
  if (section === 'memory') return <MemoryControlMount />
  if (section === 'rag') return <RagMount role={role} />
  if (section === 'graph') return <GraphMount role={role} />
  if (section === 'cache') return <CacheMount />
  if (section === 'jobs') return <JobsMount />
  if (section === 'tokenopt') return <TokenOptMount />
  if (section === 'guardrails') return <GuardrailsMount />
  if (section === 'security') return <SecurityMount />
  if (section === 'compliance') return <ComplianceMount />
  if (section === 'interop') return <InteropMount />
  if (section === 'database') return <DatabaseMount />
  if (section === 'mcp') return <McpConsoleMount />
  if (section === 'redteam') return <RedteamMount />
  if (section === 'latency') return <LatencyMount />
  if (section === 'governance') return <GovernanceMount />
  if (section === 'roles') return <RolesAccessMount />
  if (section === 'audit') return <AuditMount />
  if (section === 'stack') return <StackMount />
  if (section === 'patch') return <PatchMount />
  if (section === 'savings') return <SavingsMount />
  if (section === 'risk') return <RiskMount />
  if (section === 'settings') return <SettingsMount />
  if (section === 'simulation') return <SimulationMount />
  if (section === 'voice') return <VoiceMount />
  if (section === 'vision') return <VisionMount />
  // Overview: both admin portals get the comprehensive command center (all business
  // + governance + safety figures, composed from the live accessors, every one of
  // them tenant-scoped by the backend for the tenant admin); devops + client get the
  // money-shot metrics dashboard.
  if (section === 'dashboard' && portal === 'platform_admin') return <AdminDashboardMount />
  if (section === 'dashboard' && portal === 'tenant_admin') return <AdminDashboardMount />
  if (section === 'dashboard') return <DashboardMount role={role} />
  notFound()
}
