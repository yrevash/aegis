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
import { useEffect, useState } from 'react'

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

export function ModuleGrid() {
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
    <section id="modules" className="border-b border-border">
      <div className="mx-auto max-w-6xl px-6 py-20">
        <div className="mb-10 text-center">
          <p className="eyebrow mb-3">The platform</p>
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">
            Twelve modules. Every one names what it runs on.
          </h2>
        </div>

        <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2 lg:grid-cols-4">
          {(modules ?? Array.from({ length: 12 }, () => null)).map((m, i) => {
            const Icon = m ? (ICONS[m.name] ?? Waypoints) : Waypoints
            return (
              <div key={m?.name ?? i} className="flex items-center gap-3.5 bg-card px-5 py-5">
                {m === null ? (
                  <div className="h-9 w-full animate-pulse rounded bg-muted" />
                ) : (
                  <>
                    <Icon className="size-5 shrink-0 text-muted-foreground" strokeWidth={1.6} />
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold tracking-tight text-foreground">
                        {m.name.replace(/^Aegis /, '')}
                      </h3>
                      <p className="truncate font-mono text-[0.66rem] text-graph-ink">
                        {m.tech}
                      </p>
                    </div>
                  </>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
