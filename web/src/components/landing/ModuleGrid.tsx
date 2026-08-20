'use client'

import {
  Brain,
  DatabaseZap,
  Gauge,
  Landmark,
  Network,
  RefreshCcw,
  Route,
  ScrollText,
  ShieldCheck,
  Sigma,
  Waypoints,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { LandingSection } from './LandingSection'

import { getCapabilities } from '@/lib/api/client'
import type { AegisModuleRow } from '@/lib/api/types'

/**
 * The twelve Aegis modules, read live from `GET /platform/capabilities`.
 *
 * Each tile is the branded name plus the **real tech underneath** — branding,
 * never hiding. The manifest's one-line summary is deliberately not rendered:
 * twelve sentences turned this section into a wall of prose, and the name/tech
 * pairing is the part that carries the claim. The summaries stay one click away
 * at `/platform/capabilities`.
 *
 * If the fetch fails the section renders nothing. Showing a hardcoded module
 * list when the backend is unreachable would claim capabilities the page cannot
 * substantiate.
 *
 * The loading tiles are placeholder rows the shape of the real ones rather than
 * a spinner, so the section does not resize when twelve names arrive.
 */

/** Module name → icon. Falls back to a neutral glyph for an unknown module. */
const ICONS: Record<string, LucideIcon> = {
  'Aegis Gateway': Route,
  'Aegis Router': Waypoints,
  'Aegis Memory': Brain,
  'Aegis Cache': DatabaseZap,
  'Aegis Retrieval': Network,
  'Aegis Signal': Sigma,
  'Aegis Guardrails': ShieldCheck,
  'Aegis Evals': Gauge,
  'Aegis Loop': RefreshCcw,
  'Aegis Governance': Landmark,
  'Aegis Trace': ScrollText,
  'Aegis Tools / MCP': Wrench,
}

export function ModuleGrid(): ReactElement | null {
  const [modules, setModules] = useState<AegisModuleRow[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    getCapabilities()
      .then((r) => live && setModules(r.modules))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [])

  if (failed) return null

  return (
    <LandingSection
      id="modules"
      eyebrow="The platform"
      title="Twelve modules. Every one names what it runs on."
      note="Read from the running platform's own capability manifest, so this list cannot drift from what is installed."
    >
      <ul
        className="grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 lg:grid-cols-4"
        aria-busy={modules === null}
      >
        {(modules ?? Array.from({ length: 12 }, () => null)).map((m, i) => {
          if (m === null) {
            return (
              <li key={`placeholder-${i}`} className="bg-card px-5 py-5">
                <span className="sr-only">Loading the module list…</span>
                <div aria-hidden className="h-9 w-full animate-pulse rounded-md bg-muted" />
              </li>
            )
          }
          const Icon = ICONS[m.name] ?? Waypoints
          return (
            <li key={m.name} className="flex items-center gap-3.5 bg-card px-5 py-5">
              <Icon className="size-5 shrink-0 text-muted-foreground" strokeWidth={1.6} aria-hidden />
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold tracking-[-0.01em] text-foreground">
                  {m.name.replace(/^Aegis /, '')}
                </h3>
                <p className="truncate font-mono text-[0.68rem] text-blue-600">{m.tech}</p>
              </div>
            </li>
          )
        })}
      </ul>
    </LandingSection>
  )
}
