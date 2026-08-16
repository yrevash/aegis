import type { Metadata } from 'next'

import { ArchitectureDiagram } from '@/components/landing/ArchitectureDiagram'
import { Hero } from '@/components/landing/Hero'
import { LandingFooter } from '@/components/landing/LandingFooter'
import { LandingHeader } from '@/components/landing/LandingHeader'
import { MetricsStrip } from '@/components/landing/MetricsStrip'
import { ModuleGrid } from '@/components/landing/ModuleGrid'
import { Roadmap } from '@/components/landing/Roadmap'
import { TrustStack } from '@/components/landing/TrustStack'

export const metadata: Metadata = {
  title: 'Aegis — bounded-autonomy AI, made watchable',
  description:
    'A domain-agnostic enterprise agentic-AI platform. Every autonomous action is ' +
    'uncertainty-bounded, explainable, guarded, human-approved and fully traced.',
}

/**
 * The public landing page.
 *
 * Previously this route redirected straight to `/login`, so the first thing any
 * visitor saw was a sign-in form. It is now the product's front door; the login
 * page is unchanged and still lives at `/login`, reached from the header CTA.
 *
 * Two sections read live data — {@link ModuleGrid} from `/platform/capabilities`
 * and {@link MetricsStrip} from `/platform/public-metrics`, both public
 * endpoints. Each renders nothing rather than inventing content when the backend
 * is unreachable, so the page degrades to its substantiated claims instead of
 * advertising capabilities it cannot show.
 */
export default function Home() {
  return (
    <div className="min-h-dvh bg-background">
      <LandingHeader />
      <main>
        <Hero />
        <ModuleGrid />
        <ArchitectureDiagram />
        <TrustStack />
        <MetricsStrip />
        <Roadmap />
      </main>
      <LandingFooter />
    </div>
  )
}
