'use client'

import { Check } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { getAgentCard, type AgentCard } from '@/lib/api/client'

/**
 * Interop — the published standards this platform speaks, and where to check them.
 *
 * ## Why this page exists
 *
 * A2A, MCP and CycloneDX are the parts of Aegis that a buyer's own tooling can talk to,
 * and they were the only capabilities with **no surface at all**: real, tested, served —
 * and invisible unless someone thought to curl a well-known path. A capability nobody can
 * find has, for demo purposes, not been built.
 *
 * ## The one rule this page follows
 *
 * **The A2A block is a live probe, not a claim.** The protocol version, the interfaces and
 * the skill list are read from the running deployment on mount, so a card that stops
 * answering leaves this page saying nothing rather than continuing to advertise. A
 * marketing surface that cannot fail is one nobody should believe.
 *
 * Endpoints are printed in full, because the entire point is that a reader can check them.
 */

/** One protocol, as this build implements it. */
interface Protocol {
  id: string
  name: string
  spec: string
  /** One line. Never a paragraph — DESIGN.md §4. */
  what: string
  /** Paths a reader can hit. */
  endpoints: string[]
}

const PROTOCOLS: readonly Protocol[] = [
  {
    id: 'a2a',
    name: 'A2A',
    spec: 'Agent2Agent 1.0',
    what: 'Other agents discover this one and send it work.',
    endpoints: ['/.well-known/agent-card.json', '/.well-known/jwks.json', '/v1/a2a'],
  },
  {
    id: 'mcp',
    name: 'MCP',
    spec: 'Model Context Protocol',
    what: 'This agent’s tools, exposed to any MCP client.',
    endpoints: ['/v1/mcp'],
  },
  {
    id: 'cyclonedx',
    name: 'CycloneDX',
    spec: '1.6',
    what: 'A bill of materials for the agent, and for its dependencies.',
    endpoints: ['/v1/platform/agbom', '/v1/stack/sbom'],
  },
  {
    id: 'otel',
    name: 'OpenTelemetry',
    spec: 'GenAI semconv + OpenInference',
    what: 'Every run exported as spans your collector already reads.',
    endpoints: ['aegis.observability.semconv'],
  },
] as const

export function InteropView(): ReactElement {
  const [card, setCard] = useState<AgentCard | null>(null)
  const [probed, setProbed] = useState(false)

  useEffect(() => {
    let alive = true
    // Through the API layer, not a bare fetch: `test_route_coverage.py` asserts that
    // every endpoint the console touches is visible to its analysis, and a `fetch()`
    // in a component is invisible to it. The guard caught this one.
    void getAgentCard().then((d) => {
      if (!alive) return
      setCard(d)
      setProbed(true)
    })
    return () => {
      alive = false
    }
  }, [])

  const skills = card?.skills ?? []
  const interfaces = card?.supportedInterfaces ?? []

  return (
    <div className="flex flex-col gap-4">
      <PageHeader eyebrow="published standards" title="Interop" />

      {/* Four protocols, four marks. The `what` line is the whole description and the
          endpoint is the evidence — no paragraph earns a place here. */}
      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        {PROTOCOLS.map((p) => (
          <Card key={p.id} className="min-w-0">
            <CardHeader
              eyebrow={p.spec}
              title={p.name}
              actions={
                p.id === 'a2a' && probed ? (
                  card != null ? (
                    <Badge tone="ok" className="gap-1.5">
                      <Check className="size-3" aria-hidden />
                      answering
                    </Badge>
                  ) : (
                    <Badge tone="risk">no answer</Badge>
                  )
                ) : (
                  <Badge tone="neutral">served</Badge>
                )
              }
            />
            <CardBody className="flex min-w-0 flex-col gap-3 pt-0">
              <p className="text-sm text-foreground">{p.what}</p>
              <ul className="flex min-w-0 flex-col gap-1">
                {p.endpoints.map((e) => (
                  <li key={e}>
                    <code className="block truncate font-mono text-xs text-muted-foreground">
                      {e}
                    </code>
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        ))}
      </div>

      {/* A2A in detail, read from the card this deployment actually serves. */}
      <DataPanel
        eyebrow="a2a · /.well-known/agent-card.json"
        title="What this agent advertises"
        collapsible
        summary={
          card
            ? `${skills.length} skill${skills.length === 1 ? '' : 's'} · protocol ${card.protocolVersion ?? '?'}`
            : 'card not read'
        }
        footer={
          <Receipt
            origin="the agent card served by this deployment"
            detail="read live on mount, unauthenticated — the same request a peer agent makes"
            className="w-full border-t-0 pt-0"
          />
        }
      >
        {card == null ? (
          <p className="py-3 text-sm text-muted-foreground">
            The agent card did not answer, so nothing is claimed here.
          </p>
        ) : (
          <ul className="flex min-w-0 flex-col gap-3">
            {skills.map((s) => (
              <li key={s.id} className="flex min-w-0 items-baseline gap-3">
                <code className="shrink-0 font-mono text-xs text-muted-foreground">{s.id}</code>
                <span className="min-w-0 truncate text-sm text-foreground">{s.name}</span>
              </li>
            ))}
            {interfaces.map((i) => (
              <li key={i.url} className="flex min-w-0 items-baseline gap-3">
                <Badge tone="neutral" className="shrink-0 font-mono">
                  {i.protocolBinding}
                </Badge>
                <code className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                  {i.url}
                </code>
              </li>
            ))}
          </ul>
        )}
      </DataPanel>

      {/* The security property. It is the reason this surface is safe to publish at all,
          so it is the one thing here that is stated rather than probed. */}
      <Card className="min-w-0">
        <CardHeader
          eyebrow="a2a · the routing field is not the tenant"
          title="Why this is safe to expose"
        />
        <CardBody className="flex min-w-0 flex-col gap-3 pt-0">
          <p className="text-sm text-foreground">
            A2A’s <code className="font-mono text-xs">tenant</code> field arrives before
            authentication and is attacker-controlled. It selects which agent is addressed and
            never sets the database scope — that comes from the bearer token alone.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="block" className="gap-1.5">
              <Figure>4</Figure> spellings refused
            </Badge>
            <code className="font-mono text-xs text-muted-foreground">
              &quot;2&quot; · &quot;07&quot; · &quot;٧&quot; · &quot;abc&quot;
            </code>
          </div>
          <Receipt
            origin="backend/tests/a2a/test_tenant_refusal.py"
            detail="every refusal returns the same code and the same message, so the error cannot enumerate tenants"
            className="w-full"
          />
        </CardBody>
      </Card>
    </div>
  )
}

/** Client entry for the Interop section. */
export function InteropMount(): ReactElement {
  return <InteropView />
}
