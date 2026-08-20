'use client'

import {
  AlertTriangle,
  Loader2,
  Lock,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import type { McpConsole, McpGrantWrite, McpRisk, McpToolRow } from '@/lib/api/mcp'

import { RISKS, consequenceOf, gatesAt, tierProvenance } from './mcpConsole'

/**
 * Tool governance — the tier every tool is gated at, and who may call it.
 *
 * **Per named tool, never per server.** There is deliberately no "trust everything from
 * this peer" control: a peer can add a tool tomorrow, and it would inherit a decision
 * nobody made about it. A tool that appears later starts at high like every other.
 *
 * **The consequence is stated at the moment of the change**, not in a legend somewhere.
 * Choosing a tier below the deployment's gate floor says, in the sentence under the
 * dropdown, that this runs without a human seeing it first. A dropdown that only said
 * "low" would be asking the operator to hold the gating rule in their head.
 *
 * **Aegis's own tools are here and are read-only.** "Which tools exist and what does
 * each cost you" is one question, and answering half of it invites the reader to assume
 * the other half is missing. They are not editable because a `ToolSpec.risk` is domain
 * knowledge versioned with the code — the row names the module, so a reader can go and
 * check rather than take the screen's word for it.
 */

/** Tone and icon per tier. Colour never carries the meaning alone — the word is beside it. */
function riskBadge(risk: McpRisk): { tone: BadgeTone; icon: ReactElement } {
  if (risk === 'high') {
    return { tone: 'block', icon: <ShieldAlert className="size-3" aria-hidden /> }
  }
  if (risk === 'medium') {
    return { tone: 'risk', icon: <ShieldQuestion className="size-3" aria-hidden /> }
  }
  return { tone: 'ok', icon: <ShieldCheck className="size-3" aria-hidden /> }
}

/** One external tool: its tier, who may call it, and the decision that put it there. */
function ToolRow({
  tool,
  personas,
  gateRisk,
  busy,
  onWrite,
}: {
  tool: McpToolRow
  personas: string[]
  gateRisk: McpRisk
  busy: boolean
  onWrite: (name: string, next: McpGrantWrite) => void
}): ReactElement {
  const [risk, setRisk] = useState<McpRisk>(tool.risk)
  const [reason, setReason] = useState(tool.reason)
  const [admitted, setAdmitted] = useState<string[]>(tool.personas)

  // Re-sync when the server's answer changes underneath — the aggregate is the truth,
  // and a stale draft silently re-applying an old tier is the failure worth avoiding.
  useEffect(() => {
    setRisk(tool.risk)
    setReason(tool.reason)
    setAdmitted(tool.personas)
  }, [tool.risk, tool.reason, tool.personas])

  const badge = riskBadge(tool.risk)
  const dirty =
    risk !== tool.risk ||
    reason !== tool.reason ||
    admitted.join('|') !== tool.personas.join('|')
  const toggle = (persona: string) =>
    setAdmitted((current) =>
      current.includes(persona)
        ? current.filter((item) => item !== persona)
        : [...current, persona],
    )

  return (
    <TR>
      <TD className="align-top">
        <p className="font-mono text-xs text-foreground">{tool.name}</p>
        <p className="mt-1 max-w-sm text-xs text-muted-foreground">
          {tool.description || '—'}
        </p>
        <p className="mt-2 border-t border-border pt-1 font-mono text-[0.68rem] text-muted-foreground">
          Source: {tool.serverId} · advertised as {tool.remoteName}
        </p>
        {!tool.callableNow ? (
          <p className="mt-1 flex items-start gap-1 text-[0.68rem] text-muted-foreground">
            <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            Its server is disabled, so no agent is offered this tool.
          </p>
        ) : null}
      </TD>
      <TD className="align-top">
        <Badge tone={badge.tone} className="gap-1 whitespace-nowrap">
          {badge.icon}
          {tool.risk}
        </Badge>
        <p className="mt-2 max-w-[13rem] text-[0.68rem] text-muted-foreground">
          {tierProvenance(tool)}
        </p>
        {!gatesAt(tool.risk, gateRisk) && tool.personas.length > 0 ? (
          <p className="mt-1 flex items-start gap-1 text-[0.68rem] text-block-ink">
            <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            Runs without a human seeing it first.
          </p>
        ) : null}
      </TD>
      <TD className="align-top">
        <fieldset className="flex flex-col gap-1">
          <legend className="sr-only">Personas admitted for {tool.name}</legend>
          {personas.map((persona) => (
            <label key={persona} className="flex items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                className="size-3.5 rounded border-border accent-[color:var(--primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--primary)]"
                checked={admitted.includes(persona)}
                onChange={() => toggle(persona)}
              />
              <span className="font-mono">{persona}</span>
            </label>
          ))}
          {tool.personas.length === 0 ? (
            <span className="text-[0.68rem] text-muted-foreground">
              Nobody may call it.
            </span>
          ) : null}
        </fieldset>
      </TD>
      <TD className="align-top">
        <div className="flex w-[17rem] flex-col gap-2">
          <label className="flex flex-col gap-1">
            <span className="sr-only">Risk tier for {tool.name}</span>
            <select
              value={risk}
              onChange={(event) => setRisk(event.target.value as McpRisk)}
              className="rounded-md border border-border bg-card px-2 py-1.5 text-xs text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--primary)]"
            >
              {RISKS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <p
            className={
              gatesAt(risk, gateRisk)
                ? 'text-[0.68rem] text-muted-foreground'
                : 'flex items-start gap-1 text-[0.68rem] text-block-ink'
            }
          >
            {gatesAt(risk, gateRisk) ? null : (
              <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            )}
            <span>{consequenceOf(risk, gateRisk)}</span>
          </p>
          <Input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Why this tier"
            className="h-8 text-xs"
            aria-label={`Reason for the tier on ${tool.name}`}
          />
          <Button
            type="button"
            size="sm"
            variant={admitted.length === 0 ? 'outline' : 'default'}
            disabled={busy || !dirty}
            onClick={() => onWrite(tool.name, { personas: admitted, risk, reason })}
          >
            {busy ? <Loader2 className="mr-1 size-3 animate-spin" aria-hidden /> : null}
            {admitted.length === 0 ? 'Revoke' : 'Apply'}
          </Button>
        </div>
      </TD>
    </TR>
  )
}

export function ToolGovernance({
  data,
  busy,
  onWrite,
}: {
  data: McpConsole
  busy: string | null
  onWrite: (name: string, next: McpGrantWrite) => void
}): ReactElement {
  return (
    <>
      <Card>
        <CardHeader title="External tools" eyebrow="Tier, and who may call it" />
        <CardBody className="space-y-3">
          <p className="max-w-3xl text-sm text-muted-foreground">
            An external tool is code we did not write, reached over a network, returning
            content into an agent&apos;s context — and it offers no way to undo itself. It
            is therefore <span className="font-medium text-foreground">high</span> risk,
            stopping at the same human gate as a consequential domain write, until it is
            lowered here for a named tool. Whatever it returns passes the{' '}
            <span className="font-mono">TOOL_RESULT</span> rail before it reaches any
            prompt.
          </p>
          {data.tools.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing discovered yet. Test a connection above — reading a peer&apos;s tool
              list opens a connection to a third party, so it is a button rather than
              something that happens when this page loads.
            </p>
          ) : (
            <Table>
              <THead>
                <TH>Tool</TH>
                <TH>In force</TH>
                <TH>Personas</TH>
                <TH>Decide</TH>
              </THead>
              <TBody>
                {data.tools.map((tool) => (
                  <ToolRow
                    key={tool.name}
                    tool={tool}
                    personas={data.personas}
                    gateRisk={data.gateRisk}
                    busy={busy === tool.name}
                    onWrite={onWrite}
                  />
                ))}
              </TBody>
            </Table>
          )}
          <p className="border-t border-border pt-2 text-xs text-muted-foreground">
            Source: <span className="font-mono">GET /v1/mcp/console</span> · this
            deployment&apos;s gate floor is{' '}
            <span className="font-mono">{data.gateRisk}</span>. Grants and lowered tiers
            live in the serving process and reset to{' '}
            <span className="font-mono">high</span> on restart — the degradation is toward
            the gate, never around it.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Aegis tools" eyebrow="Declared in code, not editable here" />
        <CardBody className="space-y-3">
          <p className="max-w-3xl text-sm text-muted-foreground">
            Aegis&apos;s own tools and the tier each is gated at. There is no control here
            because a tier is declared on the tool, in the module named in each row, and
            reviewed with the code that ships it — a runtime switch that could lower one
            would be a way to walk a consequential write out of the gate over HTTP.
          </p>
          <Table>
            <THead>
              <TH>Tool</TH>
              <TH>In force</TH>
              <TH>Personas</TH>
              <TH>Declared in</TH>
            </THead>
            <TBody>
              {data.aegisTools.map((tool) => {
                const badge = riskBadge(tool.risk)
                return (
                  <TR key={tool.name}>
                    <TD className="align-top">
                      <p className="font-mono text-xs text-foreground">{tool.name}</p>
                      <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                        {tool.description}
                      </p>
                    </TD>
                    <TD className="align-top">
                      <Badge tone={badge.tone} className="gap-1 whitespace-nowrap">
                        {badge.icon}
                        {tool.risk}
                      </Badge>
                      <p className="mt-1 max-w-[13rem] text-[0.68rem] text-muted-foreground">
                        {gatesAt(tool.risk, data.gateRisk)
                          ? 'Stops at the human gate.'
                          : 'Runs unattended.'}
                      </p>
                    </TD>
                    <TD className="align-top font-mono text-xs text-muted-foreground">
                      {tool.personas.join(', ') || '—'}
                    </TD>
                    <TD className="align-top">
                      <span className="inline-flex items-start gap-1.5">
                        <Lock
                          className="mt-0.5 size-3 shrink-0 text-muted-foreground"
                          aria-hidden
                        />
                        <span className="font-mono text-[0.7rem] text-muted-foreground">
                          {tool.declaredIn}
                        </span>
                      </span>
                    </TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Decisions" eyebrow="Who lowered what, and what they said" />
        <CardBody className="space-y-3">
          {data.decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No tier has been decided in this deployment yet. Every tool is at the
              default.
            </p>
          ) : (
            <Table>
              <THead>
                <TH>When</TH>
                <TH>Who</TH>
                <TH>Tool</TH>
                <TH>Tier</TH>
                <TH>Personas</TH>
                <TH>Reason</TH>
              </THead>
              <TBody>
                {data.decisions.map((decision, index) => (
                  <TR key={`${decision.at}-${decision.tool}-${index}`}>
                    <TD className="whitespace-nowrap font-mono text-[0.7rem] text-muted-foreground">
                      {decision.at}
                    </TD>
                    <TD className="font-mono text-xs text-foreground">{decision.actor}</TD>
                    <TD className="font-mono text-xs text-foreground">{decision.tool}</TD>
                    <TD className="whitespace-nowrap font-mono text-xs">
                      <span className="text-muted-foreground">{decision.riskBefore}</span>
                      <span className="text-muted-foreground"> → </span>
                      <span className="text-foreground">{decision.riskAfter}</span>
                    </TD>
                    <TD className="font-mono text-[0.7rem] text-muted-foreground">
                      {(decision.personasBefore.join(', ') || 'none') +
                        ' → ' +
                        (decision.personasAfter.join(', ') || 'none')}
                    </TD>
                    <TD className="max-w-xs text-xs text-muted-foreground">
                      {decision.reason || '— no reason given —'}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <p className="border-t border-border pt-2 text-xs text-muted-foreground">
            Source: the audit trail, action{' '}
            <span className="font-mono">mcp.tool_risk_decided</span>. The last{' '}
            {data.decisions.length} shown here; the full history is on the audit page.
          </p>
        </CardBody>
      </Card>
    </>
  )
}
