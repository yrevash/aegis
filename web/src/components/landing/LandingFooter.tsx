import Link from 'next/link'
import type { ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { API_BASE, API_ORIGIN } from '@/lib/api/config'

/**
 * Closing band: the mark, a last way into the console, and the self-describing
 * surfaces worth knowing about.
 *
 * The links are grouped and labelled now rather than run together in one mono
 * row. Three of the four are **backend routes, not console pages** — a visitor
 * who clicks "Capabilities" lands on JSON — and mixing them with an in-app link
 * under no heading gave no way to tell which was which before clicking.
 */
export function LandingFooter(): ReactElement {
  return (
    <footer className="bg-background">
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-6 py-14 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <AegisLockup size="md" />
          <p className="mt-3 max-w-xs text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
            A domain-agnostic enterprise agentic-AI platform. The core is imported, not
            forked.
          </p>
        </div>

        <div className="flex flex-col gap-8 sm:flex-row sm:gap-14">
          <nav aria-labelledby="footer-console">
            <h2 id="footer-console" className="eyebrow mb-3">
              Console
            </h2>
            <ul className="space-y-2">
              <li>
                <Link href="/login" className={LINK}>
                  Sign in
                </Link>
              </li>
              <li>
                <a href="#architecture" className={LINK}>
                  How it works
                </a>
              </li>
            </ul>
          </nav>

          {/* Resolved against the API origin so they work whether the API is
              same-origin or on :8000. */}
          <nav aria-labelledby="footer-api">
            <h2 id="footer-api" className="eyebrow mb-3">
              The API, describing itself
            </h2>
            <ul className="space-y-2">
              <li>
                <a href={`${API_ORIGIN}/docs`} className={LINK}>
                  OpenAPI docs
                </a>
              </li>
              <li>
                <a href={`${API_BASE}/about`} className={LINK}>
                  About this build
                </a>
              </li>
              <li>
                <a href={`${API_BASE}/platform/capabilities`} className={LINK}>
                  Capability manifest
                </a>
              </li>
            </ul>
          </nav>
        </div>
      </div>
    </footer>
  )
}

/** One footer link. Sentence case, real focus ring, a target a thumb can hit. */
const LINK =
  'inline-flex min-h-8 items-center rounded-sm text-sm text-muted-foreground outline-none transition-colors duration-[--dur-fast] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'
