import { notFound } from 'next/navigation'
import { CacheMount } from '@/components/cache/CacheView'
import { ConsoleMount } from '@/components/console/ConsoleMount'
import { EvalsMount } from '@/components/evals/EvalsView'
import { TokenOptMount } from '@/components/gateway/TokenOptView'
import { MLOpsMount } from '@/components/ml/MLOpsView'
import { MemoryMount } from '@/components/memory/MemoryView'
import { LLMOpsMount } from '@/components/ops/LLMOpsView'
import { SectionPlaceholder } from '@/components/portal/SectionPlaceholder'
import { isRole, isValidSection, ROLE_SECTIONS, SECTIONS } from '@/lib/portal'

/**
 * A single portal section. Validates that `section` is exposed by `role` (RBAC),
 * then renders the Console's dedicated live-run placeholder or the generic
 * titled placeholder. generateStaticParams enumerates every role/section combo
 * so `next build` prints the full portal route tree.
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
  if (section === 'mlops') return <MLOpsMount />
  if (section === 'llmops') return <LLMOpsMount />
  if (section === 'evals') return <EvalsMount />
  if (section === 'memory') return <MemoryMount />
  if (section === 'cache') return <CacheMount />
  if (section === 'tokenopt') return <TokenOptMount />
  return <SectionPlaceholder section={def} />
}
