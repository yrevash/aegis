import type { Metadata } from 'next'

import { GovernanceSection } from '@/components/landing/GovernanceSection'
import { Hero } from '@/components/landing/Hero'
import { LandingFooter } from '@/components/landing/LandingFooter'
import { LandingHeader } from '@/components/landing/LandingHeader'
import { LivePlatform } from '@/components/landing/LivePlatform'
import { Roadmap } from '@/components/landing/Roadmap'
import { RunChain } from '@/components/landing/RunChain'
import { StackBand } from '@/components/landing/StackBand'
import { StandardsBand } from '@/components/landing/StandardsBand'
import { TeamSection } from '@/components/landing/TeamSection'
import { STACK_BAND, STANDARDS_BAND } from '@/components/landing/bands.config'

export const metadata: Metadata = {
  title: 'Aegis — autonomy you can audit',
  description:
    'A domain-agnostic, multi-tenant enterprise agentic-AI platform. A team of agents ' +
    'answers, guardrails run both ways, a person signs for consequential writes, and ' +
    'every figure carries its origin.',
}

/**
 * The public landing page.
 *
 * **The spine is one run.** The hero states the claim and says which endpoint the
 * page is reading; the chain of custody draws that run end to end and is the one
 * thing the page spends its boldness on; the three sections after it take the
 * parts the picture cannot carry — who chose the width, what stops a write, what
 * the backend said just now — and the last one names what is not built.
 *
 * Two things were removed rather than restyled. The Mermaid architecture diagram
 * went because a four-box flowchart was competing with the chain for the reader's
 * one diagram, and losing; its claim survives as a sentence in
 * {@link LivePlatform}. The six-checkpoint "trust stack" went because the chain
 * shows the same six checkpoints in the order a run meets them, and a page should
 * not make its central argument twice.
 *
 * Everything that reads a live endpoint renders nothing rather than inventing
 * content when the backend is unreachable — including the hero, which says so.
 *
 * **Two sections are switchable, and that is deliberate.** The standards band names
 * published frameworks on a public page, which is defensible only while the framing
 * stays exact — so it ships with an off switch the project owns, and the technology
 * band beside it has its own. Both constants live in
 * `components/landing/bands.config.ts`, whose docstring says precisely what each one
 * removes. They are guarded here, at the one place the page is composed, so a
 * disabled band renders nothing at all rather than an empty section: no heading, no
 * grid, no reserved space, and — for the standards band — no fetch either.
 */
export default function Home() {
  return (
    <div className="min-h-dvh bg-background">
      <LandingHeader />
      <main id="main" tabIndex={-1}>
        <Hero />
        <RunChain />
        <TeamSection />
        <GovernanceSection />
        <LivePlatform />
        {STANDARDS_BAND ? <StandardsBand /> : null}
        {STACK_BAND ? <StackBand /> : null}
        <Roadmap />
      </main>
      <LandingFooter />
    </div>
  )
}
