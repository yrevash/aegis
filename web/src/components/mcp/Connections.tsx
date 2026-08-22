'use client'

import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  KeyRound,
  Loader2,
  Network,
  Plus,
  PlugZap,
  Power,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useId, useState, type ReactElement, type ReactNode } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Input } from '@/components/primitives/input'
import { EmptyState } from '@/components/primitives/States'
import type {
  McpConsole,
  McpProbe,
  McpServerCreate,
  McpServerRow,
  McpServerUpdate,
} from '@/lib/api/mcp'
import { cn } from '@/lib/utils'

import {
  afterSubmit,
  beforeSubmit,
  refuseLocally,
  type DeclareState,
} from './declareForm'
import { PEER_STATE_TEXT, peerState, type PeerState } from './mcpConsole'

/** Badge tone per connection state. Never the only carrier — the word is beside it. */
const STATE_TONE: Record<PeerState, 'ok' | 'block' | 'neutral' | 'risk'> = {
  answered: 'ok',
  unreachable: 'block',
  untested: 'neutral',
  disabled: 'risk',
}

const STATE_ICON = {
  answered: CheckCircle2,
  unreachable: AlertTriangle,
  untested: HelpCircle,
  disabled: Power,
} as const

/** The blank declaration. One constant, so "reset" and "initial" cannot drift apart. */
const EMPTY_DRAFT: McpServerCreate = {
  serverId: '',
  label: '',
  url: '',
  authHeader: 'Authorization',
  credential: '',
}

/**
 * Connections — declare an external MCP server, prove it answers, turn it off, forget it.
 *
 * **The credential is one-way, and the form says so.** What comes back from the server is
 * a twelve-character fingerprint, never the secret; the field is a password input, is
 * cleared as soon as the registry *accepts* the declaration, and there is no response type
 * in `lib/api/mcp.ts` with anywhere to put a credential. The row shows *whether* this
 * process holds one and *who* set it, which is what an operator actually needs to know.
 * (It used to be cleared the moment the request was *sent*, which bought nothing — the
 * value is in this component's state either way — and cost the operator a retype every
 * time the registry refused the id beside it. See {@link afterSubmit}.)
 *
 * **Testing is a button, not a page load.** Reaching a peer is a request to somebody
 * else's network; doing it on render would do it on every refresh. The result is the
 * peer's own answer either way — its name, the protocol version it negotiated, the tools
 * it advertises, or the sentence explaining why it did not reply. That answer now lands
 * **on the card it belongs to** rather than in a shared strip below the table, which was
 * the one place a reader could not tell which peer had just spoken.
 *
 * **Disabling is not deleting, and it is not a softer revocation.** A disabled peer's
 * tools leave the agent's payload entirely, so a model cannot see them and cannot try
 * them — while the connection stays configured, which is what an operator wants at
 * 3am when a peer starts misbehaving.
 *
 * A six-column table of peers was six paragraphs wide and unreadable on a phone. A peer
 * is not a row of scalars — it is an object with a state, a credential, a tool count and
 * four controls — so it is a card, and the sentences that used to sit under each cell are
 * in the tips beside them.
 */
export function Connections({
  data,
  busy,
  probe,
  onCreate,
  onUpdate,
  onDelete,
  onTest,
}: {
  data: McpConsole
  busy: string | null
  probe: McpProbe | null
  /** Resolves to the registry's refusal sentence, or `null` when it accepted. */
  onCreate: (body: McpServerCreate) => Promise<string | null>
  onUpdate: (serverId: string, body: McpServerUpdate) => void
  onDelete: (serverId: string) => void
  onTest: (serverId: string) => void
}): ReactElement {
  // Drawer, draft and last verdict are one state because they change together and the
  // rules for how are in `declareForm.ts` — see there for what each transition costs.
  const [form, setForm] = useState<DeclareState<McpServerCreate>>({
    open: false,
    draft: EMPTY_DRAFT,
    notice: null,
  })
  const { draft, open: declaring, notice } = form
  const setDraft = (next: McpServerCreate): void =>
    setForm((current) => ({ ...current, draft: next }))
  const [confirming, setConfirming] = useState<string | null>(null)
  const id = useId()

  /*
    Every peer's most recent answer, not only the last one tested.

    The probe arrives from the parent as a single value, so testing a second peer used
    to erase the first peer's result from the screen — leaving a card that had just
    reported "did not answer" looking identical to one nobody had touched. Keeping the
    answers per server id means the page states, for every peer at once, whether it is
    connected. Nothing is invented: a peer with no entry renders as "not tested yet".
  */
  const [probes, setProbes] = useState<Record<string, McpProbe>>({})
  useEffect(() => {
    if (!probe) return
    setProbes((current) => ({ ...current, [probe.serverId]: probe }))
  }, [probe])

  /**
   * Declare the peer, and let the registry's answer decide what happens to the form.
   *
   * Nothing is cleared or closed until the server has spoken. A refusal keeps the
   * drawer open with every value intact and prints the reason under the field it is
   * about; only acceptance blanks the draft.
   */
  const submit = async (): Promise<void> => {
    const serverId = draft.serverId.trim()
    // Cleared first, on *every* attempt — including one that never leaves the browser,
    // which used to leave the previous verdict standing and reading as this one's.
    const attempt = beforeSubmit(form)
    if (!serverId) {
      setForm(refuseLocally(attempt, 'An id is required — it becomes the tool namespace.'))
      return
    }
    setForm(attempt)
    const reason = await onCreate({ ...attempt.draft, serverId })
    setForm((current) =>
      afterSubmit(current, { reason }, EMPTY_DRAFT, `${serverId} is declared.`),
    )
  }

  return (
    <Card>
      <CardHeader
        title="Outbound — the external servers Aegis may reach"
        eyebrow="steps 1 and 2 · declare a peer, then test it"
        actions={
          <Button
            type="button"
            variant={declaring ? 'outline' : 'default'}
            size="sm"
            aria-expanded={declaring}
            // Opening the drawer is a fresh attempt: the last verdict goes with it.
            onClick={() =>
              setForm((current) => ({ ...current, open: !current.open, notice: null }))
            }
          >
            {declaring ? (
              <X className="mr-1 size-3" aria-hidden />
            ) : (
              <Plus className="mr-1 size-3" aria-hidden />
            )}
            {declaring ? 'Close' : 'Declare a server'}
          </Button>
        }
      />
      <CardBody className="space-y-4">
        {data.servers.length > 0 ? (
          // One column from `xl` up, where the page itself puts this panel in a
          // half-width column. A media query reads the viewport, not the container,
          // so a second peer column there is two 200px cards of ellipses.
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
            {data.servers.map((server) => (
              <PeerCard
                key={server.serverId}
                server={server}
                busy={busy === server.serverId}
                probe={probes[server.serverId] ?? null}
                confirming={confirming === server.serverId}
                onConfirm={(open) => setConfirming(open ? server.serverId : null)}
                onTest={() => onTest(server.serverId)}
                onToggle={() => onUpdate(server.serverId, { enabled: !server.enabled })}
                onDelete={() => {
                  setConfirming(null)
                  onDelete(server.serverId)
                }}
                describedBy={`${id}-remove-${server.serverId}`}
              />
            ))}
          </ul>
        ) : (
          <EmptyState
            icon={Network}
            title="No external MCP server is declared"
            body="Declare one above, or set AEGIS_MCP_CLIENT_SERVERS to a comma-separated id=url list before the process starts."
          />
        )}

        {/* The verdict on the last declaration, and only ever that one. It sits outside
            the drawer so an accepted declaration — which closes the drawer — still says
            so. */}
        {notice ? (
          <p
            role="status"
            className={cn(
              'flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
              notice.kind === 'ok'
                ? 'border-ok/60 bg-ok/10 text-foreground'
                : 'border-block/60 bg-block/10 text-foreground',
            )}
          >
            {notice.kind === 'ok' ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok-ink" aria-hidden />
            ) : (
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
            )}
            <span className="min-w-0">{notice.text}</span>
          </p>
        ) : null}

        {declaring ? (
          <form
            className="grid gap-3 border-t border-border pt-4 md:grid-cols-2 xl:grid-cols-1"
            // The browser's own validation bubble would abort the submit event, and an
            // aborted submit is exactly the case that used to leave a stale verdict on
            // screen. Validation happens in `submit`, where it can say so in the banner.
            noValidate
            onSubmit={(event) => {
              event.preventDefault()
              void submit()
            }}
          >
            <Field
              htmlFor={`${id}-server-id`}
              label="Id"
              tip={
                <>
                  Lowercase letters, digits and hyphens. It becomes the tool namespace{' '}
                  <span className="font-mono">
                    mcp__{draft.serverId.trim() || 'id'}__&lt;tool&gt;
                  </span>
                  , which is what stops a peer shadowing an Aegis tool.
                </>
              }
            >
              <Input
                id={`${id}-server-id`}
                value={draft.serverId}
                onChange={(event) => setDraft({ ...draft, serverId: event.target.value })}
                placeholder="acme…"
                autoComplete="off"
                spellCheck={false}
                required
              />
            </Field>
            <Field htmlFor={`${id}-server-label`} label="Label">
              <Input
                id={`${id}-server-label`}
                value={draft.label}
                onChange={(event) => setDraft({ ...draft, label: event.target.value })}
                placeholder="Acme tools…"
                autoComplete="off"
              />
            </Field>
            <Field
              htmlFor={`${id}-server-url`}
              label="Endpoint"
              tip="The peer’s Streamable HTTP address."
            >
              <Input
                id={`${id}-server-url`}
                value={draft.url}
                onChange={(event) => setDraft({ ...draft, url: event.target.value })}
                placeholder="https://acme.example/mcp…"
                autoComplete="off"
                spellCheck={false}
                inputMode="url"
              />
            </Field>
            <Field
              htmlFor={`${id}-server-auth`}
              label="Auth header"
              tip={
                <>
                  Where the credential goes — <span className="font-mono">Authorization</span> for
                  a bearer, <span className="font-mono">X-API-Key</span> for many others.
                </>
              }
            >
              <Input
                id={`${id}-server-auth`}
                value={draft.authHeader}
                onChange={(event) => setDraft({ ...draft, authHeader: event.target.value })}
                placeholder="Authorization…"
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
            <Field
              htmlFor={`${id}-server-cred`}
              label="Credential"
              className="md:col-span-2 xl:col-span-1"
              tip={
                <>
                  Held by the serving process and nowhere else. Aegis never writes a third
                  party’s secret to its own database, so there is nothing to read back — not
                  through this API, and not out of a database dump. What is stored is a
                  fingerprint. After a restart, re-enter it or supply it as{' '}
                  <span className="font-mono">AEGIS_MCP_CRED_&lt;ID&gt;</span>.
                </>
              }
            >
              <Input
                id={`${id}-server-cred`}
                type="password"
                autoComplete="off"
                value={draft.credential}
                onChange={(event) => setDraft({ ...draft, credential: event.target.value })}
                placeholder="paste the peer’s secret…"
              />
            </Field>
            <div className="md:col-span-2 xl:col-span-1">
              {/* Disabled only while the request is in flight. A button greyed out for a
                  missing id is a control that refuses without saying why; `submit` says
                  why instead. */}
              <Button type="submit" disabled={busy === 'create'}>
                {busy === 'create' ? (
                  <Loader2 className="mr-2 size-4 animate-spin motion-reduce:animate-none" aria-hidden />
                ) : null}
                Declare server
              </Button>
            </div>
          </form>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** One labelled control, with the paragraph that used to sit under it in a tip. */
function Field({
  htmlFor,
  label,
  tip,
  className,
  children,
}: {
  htmlFor: string
  label: string
  tip?: ReactNode
  className?: string
  children: ReactNode
}): ReactElement {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="flex items-center gap-1 text-sm font-medium text-foreground">
        {label}
        {tip ? <InfoTip label={`About ${label}`}>{tip}</InfoTip> : null}
      </label>
      {children}
    </div>
  )
}

/**
 * One peer, with its real state: reachable or not, credentialled or not, how many
 * tools it advertised and how many of those anybody may actually call.
 */
function PeerCard({
  server,
  busy,
  probe,
  confirming,
  onConfirm,
  onTest,
  onToggle,
  onDelete,
  describedBy,
}: {
  server: McpServerRow
  busy: boolean
  probe: McpProbe | null
  confirming: boolean
  onConfirm: (open: boolean) => void
  onTest: () => void
  onToggle: () => void
  onDelete: () => void
  describedBy: string
}): ReactElement {
  const share =
    server.discoveredTools > 0 ? (server.grantedTools / server.discoveredTools) * 100 : 0
  const state = peerState(server, probe)
  const StateIcon = STATE_ICON[state]
  const identity =
    state === 'answered' && probe?.reachable
      ? [probe.serverName, probe.protocolVersion ? `protocol ${probe.protocolVersion}` : '']
          .filter(Boolean)
          .join(' · ')
      : ''

  return (
    <li
      className={cn(
        // `min-w-0`: this card is a grid/flex descendant, so its default
        // `min-width: auto` let its widest row — a peer URL and its status chips —
        // set the track width instead of wrapping. It put 64px of horizontal scroll
        // on the whole document at 390px.
        'flex min-w-0 flex-col gap-3 rounded-lg border p-4 transition-colors duration-[--dur-fast]',
        server.enabled ? 'border-border bg-card' : 'border-border bg-surface-2/60',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <span className="flex items-center gap-2">
            <Network className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <span className="truncate text-sm font-medium text-foreground">{server.label}</span>
          </span>
          <p className="mt-0.5 truncate font-mono text-[0.68rem] text-muted-foreground">
            {server.url || 'in-process'}
          </p>
        </div>
        <Badge tone={STATE_TONE[state]} className="shrink-0 gap-1 whitespace-nowrap">
          <StateIcon className="size-3" aria-hidden />
          {PEER_STATE_TEXT[state].label}
        </Badge>
      </div>

      {/* The state, said as a consequence rather than as an adjective. */}
      <p className="text-xs leading-5 text-muted-foreground">
        {PEER_STATE_TEXT[state].means}
        {identity ? (
          <>
            {' — '}
            <Figure className="text-[0.68rem] text-foreground">{identity}</Figure>
          </>
        ) : null}
      </p>

      {/* The three facts about a peer that are not its address. */}
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border">
        <Fact
          label="tools found"
          value={String(server.discoveredTools)}
          tip="What the peer advertised the last time Test ran in this process. Nothing is discovered when the page loads."
        />
        <Fact
          label="callable"
          value={String(server.grantedTools)}
          tip="How many of those a persona has been admitted to. Everything else is discovered and inert — an agent is never offered it."
        />
        {/* Its own row: the namespace is the longest value on the card and the one a
            reader most often wants to read in full rather than as an ellipsis. */}
        <Fact
          className="col-span-2"
          label="namespace"
          value={`mcp__${server.serverId}__`}
          mono
          tip="Every tool this peer offers is prefixed with this, which is what stops a peer shadowing an Aegis tool of the same name."
        />
      </dl>

      <div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
          role="img"
          aria-label={`${server.grantedTools} of ${server.discoveredTools} tools callable`}
        >
          <span className="block h-full bg-blue-600" style={{ width: `${share}%` }} />
        </div>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.68rem] text-muted-foreground">
          <span>
            {server.grantedTools} of {server.discoveredTools} tools callable
          </span>
          {server.hasCredential ? (
            <span className="inline-flex items-center gap-1">
              <KeyRound className="size-3 text-ok-ink" aria-hidden />
              credential
              <Figure className="text-[0.68rem]">{server.credentialFingerprint}</Figure>
              {server.credentialSetBy ? <>set by {server.credentialSetBy}</> : null}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <KeyRound className="size-3" aria-hidden />
              no credential
              {server.credentialFingerprint ? (
                <InfoTip label="Why the credential is gone">
                  One was set before this process started. Aegis never writes a peer&rsquo;s
                  secret to its database — re-enter it, or set{' '}
                  <span className="font-mono">
                    AEGIS_MCP_CRED_{server.serverId.toUpperCase().replace(/-/g, '_')}
                  </span>
                  .
                </InfoTip>
              ) : null}
            </span>
          )}
        </p>
      </div>

      {probe && !probe.reachable ? (
        <p
          role="status"
          aria-live="polite"
          className="flex items-start gap-1.5 rounded-md border border-block bg-block/10 px-3 py-2 text-xs leading-relaxed text-foreground"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-block-ink" aria-hidden />
          <span className="min-w-0">{probe.detail}</span>
        </p>
      ) : null}

      {probe?.reachable ? (
        <div role="status" aria-live="polite">
          <p className="eyebrow mb-1.5">it advertises</p>
          {probe.tools.length > 0 ? (
            <ul className="flex flex-wrap gap-1">
              {probe.tools.map((tool) => (
                <li
                  key={tool}
                  className="tabular rounded-full border border-border bg-surface-2 px-2 py-0.5 font-mono text-[0.68rem] text-foreground"
                >
                  {tool}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              nothing — it answered, and its tool list is empty
            </p>
          )}
        </div>
      ) : null}

      {confirming ? (
        <div className="rounded-md border border-block bg-block/10 p-3">
          <p id={describedBy} className="text-xs leading-relaxed text-block-ink">
            Removing {server.label} forgets its endpoint, every tool it advertised, every grant
            on those tools and its credential. Nothing here can be undone from this page.
          </p>
          <div className="mt-2 flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => onConfirm(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              aria-describedby={describedBy}
              onClick={onDelete}
            >
              Remove {server.label}
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-auto flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onTest}>
            {busy ? (
              <Loader2 className="mr-1 size-3 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <PlugZap className="mr-1 size-3" aria-hidden />
            )}
            Test
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onToggle}>
            {server.enabled ? 'Disable' : 'Enable'}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => onConfirm(true)}
          >
            <Trash2 className="mr-1 size-3" aria-hidden />
            Remove
          </Button>
        </div>
      )}
    </li>
  )
}

/** One fact about a peer: a label, a value, and the paragraph behind it in a tip. */
function Fact({
  label,
  value,
  tip,
  mono,
  className,
}: {
  label: string
  value: string
  tip: string
  mono?: boolean
  className?: string
}): ReactElement {
  return (
    <div className={cn('min-w-0 bg-card px-2.5 py-2', className)}>
      <dt className="flex items-center gap-1 text-[0.68rem] text-muted-foreground">
        {label}
        <InfoTip label={`About ${label}`}>{tip}</InfoTip>
      </dt>
      <dd className="mt-0.5 truncate">
        {mono ? (
          <Figure className="text-[0.68rem] text-foreground">{value}</Figure>
        ) : (
          <Figure className="text-foreground">{value}</Figure>
        )}
      </dd>
    </div>
  )
}
