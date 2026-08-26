'use client'

import { Check, KeyRound, PlugZap, ShieldAlert, Wrench } from 'lucide-react'
import type { ComponentType, ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { McpConsole } from '@/lib/api/mcp'
import { cn } from '@/lib/utils'

import { gatesAt } from './mcpConsole'

/**
 * The four steps, as a strip — what a peer *is*, in the order it becomes one.
 *
 * The console previously opened on a posture strip and three panels, and a reader who
 * did not already know the protocol could not tell what a "server" was, whether it was
 * connected, or what a tier bought them. None of that is a wording problem: the page
 * showed four end-states and never the path between them.
 *
 * So the path is the first thing on the page. Each step carries its own live count and
 * is marked done only when this deployment has actually reached it, which makes the
 * strip a status as well as an explanation — on an empty deployment it reads as a
 * to-do list, and on a populated one it reads as a summary.
 *
 * Nothing here is a second source of truth: every figure is counted off the same
 * `GET /v1/mcp/console` aggregate the panels below render.
 */
export function HowMcpWorks({ data }: { data: McpConsole }): ReactElement {
  const enabled = data.servers.filter((server) => server.enabled).length
  const credentialled = data.servers.filter((server) => server.hasCredential).length
  const callable = data.tools.filter(
    (tool) => tool.callableNow && tool.personas.length > 0,
  ).length
  const everyTool = [...data.tools, ...data.aegisTools]
  const unattended = everyTool.filter(
    (tool) => !gatesAt(tool.risk, data.gateRisk) && tool.personas.length > 0,
  ).length

  const steps: StepProps[] = [
    {
      icon: KeyRound,
      title: 'Declare a peer',
      body: 'An address, and optionally a secret. It grants nothing on its own.',
      value: `${data.servers.length}`,
      unit: data.servers.length === 1 ? 'peer' : 'peers',
      note: `${enabled} enabled · ${credentialled} with a credential`,
      done: data.servers.length > 0,
      tip: 'A peer is one external MCP server: an id, a URL, and a header to put a credential in. Declaring it tells Aegis where to look for tools. No tool becomes callable because a peer exists.',
    },
    {
      icon: PlugZap,
      title: 'Test it',
      body: 'Aegis opens the connection and reads the peer’s tool list.',
      value: `${data.tools.length}`,
      unit: data.tools.length === 1 ? 'tool found' : 'tools found',
      note: 'discovered on Test, never on page load',
      done: data.tools.length > 0,
      tip: 'Reaching a peer is a request to somebody else’s network, so it happens when you press Test and not when this page renders. What comes back is the peer’s own answer: its name, the protocol version it negotiated, and the tools it advertises.',
    },
    {
      icon: Wrench,
      title: 'Admit each tool',
      body: 'Every discovered tool arrives at HIGH, callable by nobody.',
      value: `${callable}`,
      unit: `of ${data.tools.length} callable`,
      note: 'admitted per named tool, never per peer',
      done: callable > 0,
      tip: 'There is deliberately no “trust everything from this peer” switch: a peer can add a tool tomorrow, and it would inherit a decision nobody made about it. A tool that appears later starts at HIGH like every other.',
    },
    {
      icon: ShieldAlert,
      title: 'Then the gate decides',
      body: `At or above ${data.gateRisk.toUpperCase()} a call waits for a person.`,
      value: `${unattended}`,
      unit: unattended === 1 ? 'runs unattended' : 'run unattended',
      note: `gate floor ${data.gateRisk}`,
      done: true,
      tip: 'The tier is not a label — it is what happens when an agent calls the tool. At or above this deployment’s floor (agent.gate_min_risk) the call stops and waits for an approval. Below it, the call runs and its result enters the answer with nobody watching.',
    },
  ]

  return (
    <Card>
      <CardHeader
        eyebrow="how a tool becomes callable here"
        title="Four steps, and nothing is skipped"
        actions={
          <InfoTip label="What this page does and does not do">
            Nothing on this page executes an external tool. That path runs through the agent,
            behind the human gate — a button here would be the side door this design exists to
            close.
          </InfoTip>
        }
      />
      <CardBody>
        <ol className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2 xl:grid-cols-4">
          {steps.map((step, index) => (
            <Step key={step.title} {...step} index={index + 1} />
          ))}
        </ol>
      </CardBody>
    </Card>
  )
}

interface StepProps {
  icon: ComponentType<{ className?: string }>
  title: string
  body: string
  value: string
  unit: string
  note: string
  done: boolean
  tip: string
}

/** One step: its ordinal, whether this deployment has reached it, and its own count. */
function Step({
  icon: Icon,
  title,
  body,
  value,
  unit,
  note,
  done,
  tip,
  index,
}: StepProps & { index: number }): ReactElement {
  return (
    <li className="flex flex-col gap-2 bg-card p-4">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'grid size-5 shrink-0 place-items-center rounded-full text-[0.6875rem] font-semibold',
            done ? 'bg-blue-600 text-white' : 'border border-border bg-surface-2 text-muted-foreground',
          )}
          aria-hidden
        >
          {done ? <Check className="size-3" /> : index}
        </span>
        <span className="flex min-w-0 items-center gap-1 text-sm font-medium text-foreground">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" />
          {title}
          <InfoTip label={`About step ${index}: ${title}`}>{tip}</InfoTip>
        </span>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">{body}</p>
      <p className="mt-auto flex items-baseline gap-1.5">
        <Figure size="stat" className="text-foreground">
          {value}
        </Figure>
        <span className="text-xs text-muted-foreground">{unit}</span>
      </p>
      <p className="text-[0.6875rem] text-muted-foreground">{note}</p>
    </li>
  )
}
