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
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Input } from '@/components/primitives/input'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { EmptyState } from '@/components/primitives/States'
import type { McpConsole, McpGrantWrite, McpRisk, McpToolRow } from '@/lib/api/mcp'

import { RISKS, consequenceOf, gatesAt, tierProvenance } from './mcpConsole'

/**
 * Chrome adds a scroll container's overflowing content to the **document's** own
 * scroll extent unless that container is positioned. `DataPanel`'s scroll box is
 * `position: static`, so a 200-row table inside a 30rem panel left the page
 * 10,948px tall — nine thousand of them empty — while the panel itself correctly
 * scrolled at 480px. Measured in Chrome 1440x1000: `box.style.position =
 * 'relative'` takes the document from 10,948px back to 2,232px.
 *
 * The real fix is one word in `components/ui/DataPanel.tsx`, which this lane does
 * not own; this is the same fix applied through the `className` the component
 * already exposes, targeting the scroll box by the `role="group"` it is given
 * whenever `maxHeight` is set. Remove it once the primitive carries it.
 */
const SCROLL_BOX = '[&>[data-slot=card-body]>[role=group]]:relative'

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
 * "low" would be asking the operator to hold the gating rule in their head. Lowering a
 * tool out of the gate is a **privilege-lowering action**, so the moment the draft would
 * do it the cell says so on a marked surface, and the button stops saying `Apply` and
 * starts saying what it will do — `Lower to LOW · runs unattended`.
 *
 * That sentence stays exactly where it was, because it is the one piece of prose on this
 * screen that is read *at the moment a decision is made*. What went was the four
 * sentences repeated on **every** row underneath it — the tier's provenance, the "runs
 * without a human seeing it first" warning, the "its server is disabled" note, and the
 * two paragraphs above the table. A consequence a reader has already read nine times is
 * not a warning any more, it is wallpaper. Those are now one {@link InfoTip} per column
 * plus a badge with an icon and a word, which is DESIGN.md §4's rule applied to the
 * densest screen in the product.
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

/** One column heading, carrying the sentence that used to repeat on every row. */
function ColumnTip({
  children,
  tip,
  className,
}: {
  children: string
  tip: string
  className?: string
}): ReactElement {
  return (
    <TH className={className}>
      <span className="inline-flex items-center gap-1">
        {children}
        <InfoTip label={`About the ${children} column`}>{tip}</InfoTip>
      </span>
    </TH>
  )
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
  const unattended = !gatesAt(tool.risk, gateRisk) && tool.personas.length > 0
  const toggle = (persona: string): void =>
    setAdmitted((current) =>
      current.includes(persona)
        ? current.filter((item) => item !== persona)
        : [...current, persona],
    )

  return (
    <TR>
      <TD className="align-top">
        <span className="flex items-center gap-1.5">
          <Figure className="text-foreground">{tool.name}</Figure>
          {tool.description ? (
            <InfoTip label={`What ${tool.name} does`}>{tool.description}</InfoTip>
          ) : null}
          {!tool.callableNow ? (
            <Badge tone="neutral" className="gap-1">
              <AlertTriangle className="size-3" aria-hidden />
              server off
              <InfoTip label="Why this tool is not offered">
                Its server is disabled, so no agent is offered this tool. The grant below stays
                configured and takes effect the moment the peer is enabled again.
              </InfoTip>
            </Badge>
          ) : null}
        </span>
        <Receipt
          origin={tool.serverId}
          detail={`advertised as ${tool.remoteName}`}
          variant="inline"
          className="mt-1"
        />
      </TD>
      <TD className="align-top">
        <span className="flex flex-col items-start gap-1">
          <Badge tone={badge.tone} className="gap-1 whitespace-nowrap">
            {badge.icon}
            {tool.risk}
            <InfoTip label={`Why ${tool.name} sits at ${tool.risk}`}>
              {tierProvenance(tool)}
            </InfoTip>
          </Badge>
          {unattended ? (
            <Badge tone="block" className="gap-1 whitespace-nowrap">
              <AlertTriangle className="size-3" aria-hidden />
              unattended
            </Badge>
          ) : null}
        </span>
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
            <span className="text-[0.68rem] text-muted-foreground italic">nobody may call it</span>
          ) : null}
        </fieldset>
      </TD>
      <TD className="align-top">
        <div className="flex w-[17rem] flex-col gap-2">
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

          {/* The consequence, at the moment of the change. This is the one sentence on
              the screen that is read while a decision is being made, so it is the one
              that stays on the page rather than moving into a tip. */}
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
      <DataPanel
        title="External tools"
        eyebrow="tier, and who may call it"
        maxHeight={data.tools.length > 4 ? '38rem' : undefined}
        className={SCROLL_BOX}
        actions={
          <InfoTip label="Why an external tool starts at high">
            An external tool is code we did not write, reached over a network, returning
            content into an agent’s context — and it offers no way to undo itself. It is
            therefore HIGH risk, stopping at the same human gate as a consequential domain
            write, until it is lowered here for a named tool. Whatever it returns passes the
            TOOL_RESULT rail before it reaches any prompt.
          </InfoTip>
        }
      >
        {data.tools.length === 0 ? (
          data.servers.length === 0 ? (
            // No peer is declared, so "no tool discovered" is not news — it is the
            // same fact stated a second time. A full empty state here made the
            // screen three tall blanks in a row; one line says it and moves on.
            <Absence
              figure="External tools"
              why="no peer is declared, so there is nothing to discover from"
              needed="Declare a server above, then press Test on it."
            />
          ) : (
            <EmptyState
              icon={Network}
              title="No external tool discovered yet"
              body="Reading a peer’s tool list opens a connection to a third party, so it happens when you ask for it and not when this page loads. Test a connection above, and everything it advertises lands here at high risk, callable by nobody."
            />
          )
        ) : (
          <Table>
            <THead>
              <ColumnTip tip="The namespaced name an agent sees, and the peer that advertised it. The namespace is what stops a peer shadowing an Aegis tool.">
                Tool
              </ColumnTip>
              <ColumnTip tip="The tier the platform is enforcing right now, and whether it stops at the human gate. An `unattended` badge means a call runs with nobody watching.">
                In force
              </ColumnTip>
              <ColumnTip tip="Which personas may call it. Admitting nobody is a valid, enforced state — a discovered tool that nobody is admitted to is inert.">
                Personas
              </ColumnTip>
              <ColumnTip tip="The change you are about to make, with its consequence stated before you press the button. Nothing here applies until you do.">
                Decide
              </ColumnTip>
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
      </DataPanel>

      <DataPanel
        title="Aegis tools"
        eyebrow="declared in code, not editable here"
        actions={
          <InfoTip label="Why these have no control">
            A tier is declared on the tool, in the module named in each row, and reviewed with
            the code that ships it. A runtime switch that could lower one would be a way to
            walk a consequential write out of the gate over HTTP.
          </InfoTip>
        }
      >
        <Table>
          <THead>
            <ColumnTip tip="Aegis’s own tools — the ones the agent reaches without leaving this deployment.">
              Tool
            </ColumnTip>
            <ColumnTip tip="The tier declared on the tool, and whether it stops at this deployment’s human gate.">
              In force
            </ColumnTip>
            <ColumnTip tip="Which personas the tool is offered to. A persona not listed never sees the tool at all.">
              Personas
            </ColumnTip>
            <ColumnTip tip="The module the tier is declared in, so a reader can check the claim rather than take this screen’s word for it.">
              Declared in
            </ColumnTip>
          </THead>
          <TBody>
            {data.aegisTools.map((tool) => {
              const badge = riskBadge(tool.risk)
              return (
                <TR key={tool.name}>
                  <TD className="align-top">
                    <span className="flex items-center gap-1.5">
                      <Figure className="text-foreground">{tool.name}</Figure>
                      {tool.description ? (
                        <InfoTip label={`What ${tool.name} does`}>{tool.description}</InfoTip>
                      ) : null}
                    </span>
                  </TD>
                  <TD className="align-top">
                    <Badge tone={badge.tone} className="gap-1 whitespace-nowrap">
                      {badge.icon}
                      {tool.risk}
                    </Badge>
                    <p className="mt-1 text-[0.68rem] text-muted-foreground">
                      {gatesAt(tool.risk, data.gateRisk) ? 'stops at the gate' : 'unattended'}
                    </p>
                  </TD>
                  <TD className="align-top">
                    <Figure className="text-muted-foreground">
                      {tool.personas.join(', ') || 'nobody'}
                    </Figure>
                  </TD>
                  <TD className="align-top">
                    <span className="inline-flex items-start gap-1.5">
                      <Lock className="mt-1 size-3 shrink-0 text-muted-foreground" aria-hidden />
                      <Figure className="text-muted-foreground">{tool.declaredIn}</Figure>
                    </span>
                  </TD>
                </TR>
              )
            })}
          </TBody>
        </Table>
      </DataPanel>

      <DataPanel
        title="Decisions"
        eyebrow="who lowered what, and what they said"
        maxHeight={data.decisions.length > 6 ? '24rem' : undefined}
        className={SCROLL_BOX}
        footer={
          <Receipt
            origin="audit trail · mcp.tool_risk_decided"
            detail={`last ${data.decisions.length} shown here; the full history is on the audit page`}
          />
        }
      >
        {data.decisions.length === 0 ? (
          data.tools.length === 0 ? (
            <Absence
              figure="Tier decisions"
              why="no tool has been lowered, because none has been discovered"
              needed="The actor, the change and the reason land here and in the audit trail the first time one is."
            />
          ) : (
            <EmptyState
              icon={ShieldCheck}
              title="No tier has been decided here"
              body="Every tool is at the default this deployment gave it. The first time somebody lowers one, the change, the actor and the reason land in this list and in the audit trail."
            />
          )
        ) : (
          <Table>
            <THead>
              <TH>When</TH>
              <TH>Who</TH>
              <TH>Tool</TH>
              <ColumnTip tip="The tier before the change and after it. A move downward is a privilege being lowered, and it is recorded with the actor who did it.">
                Tier
              </ColumnTip>
              <TH>Personas</TH>
              <ColumnTip tip="What the operator typed at the moment of the change. An empty reason is shown as empty rather than filled in.">
                Reason
              </ColumnTip>
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
                    {decision.reason || (
                      <span className="italic">no reason given</span>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </DataPanel>
    </>
  )
}
