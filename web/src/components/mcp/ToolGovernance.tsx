'use client'

import {
  AlertTriangle,
  Loader2,
  Lock,
  Network,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
} from 'lucide-react'
import { useEffect, useId, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { Input } from '@/components/primitives/input'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState } from '@/components/primitives/States'
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
 * That sentence is necessary and it was not sufficient, because it sat in the same
 * quiet grey as the four other lines in the cell. Lowering a tool out of the gate is a
 * **privilege-lowering action**, so the moment the draft would do it the cell says so
 * on a marked surface, and the button stops saying `Apply` and starts saying what it
 * will do — `Lower to LOW · runs unattended`. Revoking says the same way what it takes
 * away. A control whose label is the verb and not the outcome is a control somebody
 * presses twice.
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

/**
 * What pressing the button will do, said as the outcome rather than as the verb.
 *
 * Three cases, and the middle one is why this exists: a draft that moves a tool from
 * gated to ungated is not an edit, it is a privilege being lowered, and the label is
 * the last place a reader looks before it happens.
 */
function actionLabel(
  admitted: readonly string[],
  risk: McpRisk,
  gateRisk: McpRisk,
  loweringOutOfGate: boolean,
): string {
  if (admitted.length === 0) return 'Revoke · nobody may call it'
  if (loweringOutOfGate) return `Lower to ${risk.toUpperCase()} · runs unattended`
  return gatesAt(risk, gateRisk) ? `Apply · stops at the gate` : 'Apply'
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
  const id = useId()

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
  // The draft takes this tool out of the gate that currently holds it. That is the one
  // state on this page where a person is about to remove a protection.
  const loweringOutOfGate =
    admitted.length > 0 && !gatesAt(risk, gateRisk) && gatesAt(tool.risk, gateRisk)
  const consequenceId = `${id}-consequence`
  const toggle = (persona: string) =>
    setAdmitted((current) =>
      current.includes(persona)
        ? current.filter((item) => item !== persona)
        : [...current, persona],
    )

  return (
    <TR>
      <TD className="align-top">
        <Figure className="text-foreground">{tool.name}</Figure>
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
          {tool.description || '—'}
        </p>
        <Receipt
          origin={tool.serverId}
          detail={`advertised as ${tool.remoteName}`}
          className="mt-2 pt-2"
        />
        {!tool.callableNow ? (
          <p className="mt-1.5 flex items-start gap-1.5 text-[0.68rem] leading-snug text-muted-foreground">
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
        <p className="mt-2 max-w-[13rem] text-[0.68rem] leading-snug text-muted-foreground">
          {tierProvenance(tool)}
        </p>
        {!gatesAt(tool.risk, gateRisk) && tool.personas.length > 0 ? (
          <p className="mt-1.5 flex items-start gap-1.5 text-[0.68rem] leading-snug font-medium text-block-ink">
            <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            Runs without a human seeing it first.
          </p>
        ) : null}
      </TD>
      <TD className="align-top">
        <fieldset className="flex flex-col gap-1">
          <legend className="sr-only">Personas admitted for {tool.name}</legend>
          {personas.map((persona) => (
            <label
              key={persona}
              htmlFor={`${id}-persona-${persona}`}
              className="flex items-center gap-2 text-xs text-foreground"
            >
              <input
                id={`${id}-persona-${persona}`}
                type="checkbox"
                className="size-3.5 rounded border-border accent-[color:var(--primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--primary)]"
                checked={admitted.includes(persona)}
                onChange={() => toggle(persona)}
              />
              <span className="font-mono">{persona}</span>
            </label>
          ))}
          {tool.personas.length === 0 ? (
            <span className="text-[0.68rem] text-muted-foreground">Nobody may call it.</span>
          ) : null}
        </fieldset>
      </TD>
      <TD className="align-top">
        <div className="flex w-[18rem] flex-col gap-2">
          <label htmlFor={`${id}-risk`} className="eyebrow">
            Risk tier
          </label>
          <select
            id={`${id}-risk`}
            aria-describedby={consequenceId}
            value={risk}
            onChange={(event) => setRisk(event.target.value as McpRisk)}
            className="rounded-lg border border-border bg-card px-2 py-1.5 text-xs text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--primary)]"
          >
            {RISKS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>

          {/* The consequence, at the moment of the change. On a lowering it is not one
              more grey line in a dense cell — it is the thing being decided. */}
          <p
            id={consequenceId}
            className={
              loweringOutOfGate
                ? 'flex items-start gap-1.5 rounded-lg border border-block/60 bg-block/10 px-2 py-1.5 text-[0.68rem] leading-relaxed font-medium text-block-ink'
                : gatesAt(risk, gateRisk)
                  ? 'text-[0.68rem] leading-relaxed text-muted-foreground'
                  : 'flex items-start gap-1.5 text-[0.68rem] leading-relaxed text-block-ink'
            }
          >
            {gatesAt(risk, gateRisk) ? null : (
              <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
            )}
            <span>{consequenceOf(risk, gateRisk)}</span>
          </p>

          <label htmlFor={`${id}-reason`} className="sr-only">
            Reason for the tier on {tool.name}
          </label>
          <Input
            id={`${id}-reason`}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Why this tier…"
            autoComplete="off"
            className="h-8 rounded-lg text-xs"
          />
          <Button
            type="button"
            size="sm"
            variant={admitted.length === 0 || loweringOutOfGate ? 'outline' : 'default'}
            className={
              admitted.length === 0 || loweringOutOfGate
                ? 'border-block/60 text-block-ink hover:bg-block/10 hover:text-block-ink'
                : undefined
            }
            aria-describedby={consequenceId}
            disabled={busy || !dirty}
            onClick={() => onWrite(tool.name, { personas: admitted, risk, reason })}
          >
            {busy ? (
              <Loader2 className="mr-1 size-3 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : null}
            {actionLabel(admitted, risk, gateRisk, loweringOutOfGate)}
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
      <Card className="rounded-lg">
        <CardHeader title="External tools" eyebrow="Tier, and who may call it" />
        <CardBody className="space-y-3">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            An external tool is code we did not write, reached over a network, returning
            content into an agent’s context — and it offers no way to undo itself. It
            is therefore <span className="font-medium text-foreground">high</span> risk,
            stopping at the same human gate as a consequential domain write, until it is
            lowered here for a named tool. Whatever it returns passes the{' '}
            <span className="font-mono">TOOL_RESULT</span> rail before it reaches any
            prompt.
          </p>
          {data.tools.length === 0 ? (
            <EmptyState
              icon={Network}
              title="No external tool discovered yet"
              body="Reading a peer’s tool list opens a connection to a third party, so it happens when you ask for it and not when this page loads."
              action={
                <p className="text-sm text-muted-foreground">
                  Test a connection above, and everything it advertises lands here at high risk,
                  callable by nobody.
                </p>
              }
            />
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
          <Receipt
            origin="GET /v1/mcp/console"
            detail={`gate floor ${data.gateRisk} · grants and lowered tiers live in the serving process and reset to high on restart, so the degradation is toward the gate and never around it`}
          />
        </CardBody>
      </Card>

      <Card className="rounded-lg">
        <CardHeader title="Aegis tools" eyebrow="Declared in code, not editable here" />
        <CardBody className="space-y-3">
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            Aegis’s own tools and the tier each is gated at. There is no control here
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
                      <Figure className="text-foreground">{tool.name}</Figure>
                      <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
                        {tool.description}
                      </p>
                    </TD>
                    <TD className="align-top">
                      <Badge tone={badge.tone} className="gap-1 whitespace-nowrap">
                        {badge.icon}
                        {tool.risk}
                      </Badge>
                      <p className="mt-1.5 max-w-[13rem] text-[0.68rem] leading-snug text-muted-foreground">
                        {gatesAt(tool.risk, data.gateRisk)
                          ? 'Stops at the human gate.'
                          : 'Runs unattended.'}
                      </p>
                    </TD>
                    <TD className="align-top">
                      <Figure className="text-muted-foreground">
                        {tool.personas.join(', ') || '—'}
                      </Figure>
                    </TD>
                    <TD className="align-top">
                      <span className="inline-flex items-start gap-1.5">
                        <Lock
                          className="mt-1 size-3 shrink-0 text-muted-foreground"
                          aria-hidden
                        />
                        <Figure className="text-muted-foreground">{tool.declaredIn}</Figure>
                      </span>
                    </TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        </CardBody>
      </Card>

      <Card className="rounded-lg">
        <CardHeader title="Decisions" eyebrow="Who lowered what, and what they said" />
        <CardBody className="space-y-3">
          {data.decisions.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="No tier has been decided here"
              body="Every tool is at the default this deployment gave it. The first time somebody lowers one, the change, the actor and the reason land in this list and in the audit trail."
            />
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
                    <TD className="whitespace-nowrap">
                      <Figure className="text-muted-foreground">{decision.at}</Figure>
                    </TD>
                    <TD>
                      <Figure className="text-foreground">{decision.actor}</Figure>
                    </TD>
                    <TD>
                      <Figure className="text-foreground">{decision.tool}</Figure>
                    </TD>
                    <TD className="whitespace-nowrap">
                      <Figure className="text-muted-foreground">{decision.riskBefore}</Figure>
                      <span className="px-1 text-muted-foreground" aria-hidden>
                        →
                      </span>
                      <Figure className="text-foreground">{decision.riskAfter}</Figure>
                    </TD>
                    <TD>
                      <Figure className="text-muted-foreground">
                        {(decision.personasBefore.join(', ') || 'none') +
                          ' → ' +
                          (decision.personasAfter.join(', ') || 'none')}
                      </Figure>
                    </TD>
                    <TD className="max-w-xs text-xs leading-relaxed text-muted-foreground">
                      {decision.reason || '— no reason given —'}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
          <Receipt
            origin="audit trail · mcp.tool_risk_decided"
            detail={`last ${data.decisions.length} shown here; the full history is on the audit page`}
          />
        </CardBody>
      </Card>
    </>
  )
}
