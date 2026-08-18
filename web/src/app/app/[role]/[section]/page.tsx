import { notFound } from 'next/navigation'
import { AuditMount } from '@/components/admin/AuditLog'
import { RolesAccessMount } from '@/components/admin/RolesAccess'
import { CacheMount } from '@/components/cache/CacheView'
import { RiskMount } from '@/components/client/RiskMap'
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
import { JobsMount } from '@/components/jobs/JobsView'
import { HarnessMount } from '@/components/harness/HarnessView'
import { LatencyMount } from '@/components/latency/LatencyView'
import { MLOpsMount } from '@/components/ml/MLOpsView'
import { MemoryMount } from '@/components/memory/MemoryView'
import { LLMOpsMount } from '@/components/ops/LLMOpsView'
import { RagMount } from '@/components/retrieval/RagView'
import { RedteamMount } from '@/components/redteam/RedteamView'
import { SecurityMount } from '@/components/security/SecurityView'
import { SimulationMount } from '@/components/sim/SimulationView'
import { VisionMount } from '@/components/vision/VisionView'
import { VoiceMount } from '@/components/voice/VoiceView'
import { isRole, isValidSection, ROLE_SECTIONS, SECTIONS } from '@/lib/portal'

/**
 * A single portal section. Validates that `section` is exposed by `role` (RBAC),
 * then renders that section's live surface — every section in ROLE_SECTIONS has
 * one, so an unmatched slug is a 404 rather than an empty shell.
 * generateStaticParams enumerates every role/section combo so `next build`
 * prints the full portal route tree.
 */
export function generateStaticParams(): Array<{ role: string; section: string }> {
  return Object.entries(ROLE_SECTIONS).flatMap(([role, sections]) =>
    sections.map((section) => ({ role, section })),
  )
}

export default async function SectionPage({
  params,
}: {
  params: Promise<{ role: string; section: string }>
}) {
  const { role, section } = await params
  if (!isRole(role) || !isValidSection(role, section)) notFound()

  const def = SECTIONS[section]
  if (def.console) return <ConsoleMount role={role} />
  if (section === 'harness') return <HarnessMount role={role} />
  if (section === 'mlops') return <MLOpsMount />
  if (section === 'llmops') return <LLMOpsMount />
  if (section === 'evals') return <EvalsMount />
  if (section === 'forecast') return <ForecastMount role={role} />
  if (section === 'memory') return <MemoryMount />
  if (section === 'rag') return <RagMount role={role} />
  if (section === 'graph') return <GraphMount role={role} />
  if (section === 'cache') return <CacheMount />
  if (section === 'jobs') return <JobsMount />
  if (section === 'tokenopt') return <TokenOptMount />
  if (section === 'guardrails') return <GuardrailsMount />
  if (section === 'security') return <SecurityMount />
  if (section === 'redteam') return <RedteamMount />
  if (section === 'latency') return <LatencyMount />
  if (section === 'governance') return <GovernanceMount />
  if (section === 'roles') return <RolesAccessMount />
  if (section === 'audit') return <AuditMount />
  if (section === 'stack') return <StackMount />
  if (section === 'patch') return <PatchMount />
  if (section === 'savings') return <SavingsMount />
  if (section === 'risk') return <RiskMount />
  if (section === 'simulation') return <SimulationMount />
  if (section === 'voice') return <VoiceMount />
  if (section === 'vision') return <VisionMount />
  // Overview: the admin portal gets the comprehensive command center (all
  // business + governance + safety figures, composed from the live accessors);
  // devops + client get the money-shot metrics dashboard.
  if (section === 'dashboard' && role === 'admin') return <AdminDashboardMount />
  if (section === 'dashboard') return <DashboardMount role={role} />
  notFound()
}
