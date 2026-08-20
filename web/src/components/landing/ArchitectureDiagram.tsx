import type { ReactElement } from 'react'

import { Mermaid } from '@/components/landing/Mermaid'

import { LandingSection } from './LandingSection'

/**
 * The real architecture, rendered with Mermaid.
 *
 * The chart below is the four-layer diagram from `docs/learn/10-architecture.md`
 * — the one written against the actual tree and kept current with it — rather
 * than a simplified drawing made for this page. Every box names a real
 * directory or module, so the diagram cannot flatter the system: if the layers
 * move, this is wrong and visibly so.
 */

// One wide node per layer rather than a subgraph per layer: Mermaid ignores a
// subgraph's `direction` once the subgraph itself carries an edge, so nested
// nodes stack into a tall narrow column with dead space either side.
const ARCHITECTURE = `flowchart TB
    B["<b>Browser</b>"]
    L1["<b>1 · Console</b> — web/<br/>Next.js 15 · React 19 · TypeScript<br/>four role portals · REST + SSE client · backend health probe"]
    L2["<b>2 · Composition root</b> — backend/src/app<br/>FastAPI · app factory · background sweepers<br/>routes.py — endpoints · JWT · RBAC · tenant scoping"]
    L3["<b>3 · Importable core</b> — aegis/src/aegis<br/>agent · gateway · guardrails · retrieval · memory · ml<br/>governance · ops · evals · observability · redteam · data · core"]
    L4["<b>4 · Stores and sinks</b><br/>Postgres · embedded vectors · Neo4j · Redis · Arize Phoenix"]
    AD["<b>Domain adapter</b> — app/adapter/<br/>schema · tools · prompts · ML target · corpus"]

    B -->|"HTTPS · JWT · SSE"| L1
    L1 -->|"fetch + SSE"| L2
    L2 -->|"imports · injected deps"| L3
    L3 -->|"async drivers"| L4
    AD -.->|"the only seam that<br/>changes per domain"| L2

    classDef seam stroke:#1570ef,color:#1570ef,stroke-dasharray:4 3;
    class AD seam;
`

export function ArchitectureDiagram(): ReactElement {
  return (
    <LandingSection
      id="architecture"
      eyebrow="Architecture"
      title="Four layers. The domain plugs into one seam."
      note="The core is a package you import, not an application you fork — point it at a new domain by writing one adapter."
      className="bg-surface"
    >
      {/* The diagram is wider than a phone. It scrolls in its own container so the
          page body never does — DESIGN.md §5. */}
      <div className="overflow-x-auto rounded-lg border border-border bg-card p-4 sm:p-6">
        <Mermaid chart={ARCHITECTURE} className="mx-auto min-w-[560px] [&_svg]:mx-auto" />
      </div>
    </LandingSection>
  )
}
