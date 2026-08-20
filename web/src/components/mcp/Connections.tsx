'use client'

import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Loader2,
  Network,
  Plus,
  PlugZap,
  Power,
  Trash2,
  X,
} from 'lucide-react'
import { useId, useState, type ReactElement, type ReactNode } from 'react'

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

/**
 * Connections — declare an external MCP server, prove it answers, turn it off, forget it.
 *
 * **The credential is one-way, and the form says so.** What comes back from the server is
 * a twelve-character fingerprint, never the secret; the field is a password input, is
 * cleared the moment the request is sent, and there is no response type in
 * `lib/api/mcp.ts` with anywhere to put a credential. The row shows *whether* this
 * process holds one and *who* set it, which is what an operator actually needs to know.
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
  onCreate: (body: McpServerCreate) => void
  onUpdate: (serverId: string, body: McpServerUpdate) => void
  onDelete: (serverId: string) => void
  onTest: (serverId: string) => void
}): ReactElement {
  const [draft, setDraft] = useState<McpServerCreate>({
    serverId: '',
    label: '',
    url: '',
    authHeader: 'Authorization',
    credential: '',
  })
  const [declaring, setDeclaring] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const id = useId()

  const submit = (): void => {
    onCreate({ ...draft, serverId: draft.serverId.trim() })
    setDraft({ serverId: '', label: '', url: '', authHeader: 'Authorization', credential: '' })
    setDeclaring(false)
  }

  return (
    <Card>
      <CardHeader
        title="Connections"
        eyebrow="external MCP servers · peers"
        actions={
          <Button
            type="button"
            variant={declaring ? 'outline' : 'default'}
            size="sm"
            aria-expanded={declaring}
            onClick={() => setDeclaring((open) => !open)}
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
          <ul className="grid gap-3 xl:grid-cols-2">
            {data.servers.map((server) => (
              <PeerCard
                key={server.serverId}
                server={server}
                busy={busy === server.serverId}
                probe={probe?.serverId === server.serverId ? probe : null}
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
            body="Every peer whose tools an agent here may reach is listed on this panel. Declare one above, or set AEGIS_MCP_CLIENT_SERVERS to a comma-separated id=url list before the process starts."
          />
        )}

        {declaring ? (
          <form
            className="grid gap-3 border-t border-border pt-4 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault()
              submit()
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
              className="md:col-span-2"
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
            <div className="md:col-span-2">
              <Button type="submit" disabled={busy === 'create' || !draft.serverId.trim()}>
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

  return (
    <li
      className={cn(
        'flex flex-col gap-3 rounded-lg border p-4 transition-colors duration-[--dur-fast]',
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
            mcp__{server.serverId}__ · {server.url || 'in-process'}
          </p>
        </div>
        {server.enabled ? (
          <Badge tone="ok" className="gap-1">
            <CheckCircle2 className="size-3" aria-hidden />
            enabled
          </Badge>
        ) : (
          <Badge tone="risk" className="gap-1">
            <Power className="size-3" aria-hidden />
            disabled
            <InfoTip label="What disabled means">
              Its tools are not offered to any agent — they leave the payload entirely, so a
              model cannot see them and cannot try them. The connection stays configured.
            </InfoTip>
          </Badge>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {server.hasCredential ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <KeyRound className="size-3.5 text-ok-ink" aria-hidden />
            credential set
            <Figure className="text-[0.68rem]">{server.credentialFingerprint}</Figure>
            {server.credentialSetBy ? <>by {server.credentialSetBy}</> : null}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <KeyRound className="size-3.5" aria-hidden />
            no credential
            {server.credentialFingerprint ? (
              <InfoTip label="Why the credential is gone">
                One was set before this process started. Aegis never writes a peer&rsquo;s secret
                to its database — re-enter it, or set{' '}
                <span className="font-mono">
                  AEGIS_MCP_CRED_{server.serverId.toUpperCase().replace(/-/g, '_')}
                </span>
                .
              </InfoTip>
            ) : null}
          </span>
        )}
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Figure className="text-foreground">{server.grantedTools}</Figure>
          callable of
          <Figure className="text-foreground">{server.discoveredTools}</Figure>
          found
          <InfoTip label="Found versus callable">
            Found is what the peer advertised at the last Test. Callable is how many of those
            a persona has been admitted to — everything else is discovered and inert.
          </InfoTip>
        </span>
      </div>

      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`${server.grantedTools} of ${server.discoveredTools} tools callable`}
      >
        <span className="block h-full bg-blue-600" style={{ width: `${share}%` }} />
      </div>

      {probe ? (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            'rounded-md border px-3 py-2 text-xs leading-relaxed',
            probe.reachable ? 'border-ok bg-ok/10' : 'border-block bg-block/10',
          )}
        >
          {probe.reachable ? (
            <>
              <p className="flex items-center gap-1.5 text-foreground">
                <CheckCircle2 className="size-3.5 shrink-0 text-ok-ink" aria-hidden />
                Answered
                {probe.serverName ? ` as ${probe.serverName}` : ''}
                {probe.protocolVersion ? ` on protocol ${probe.protocolVersion}` : ''}.
              </p>
              <p className="mt-1 font-mono text-[0.68rem] text-muted-foreground">
                {probe.tools.length > 0 ? probe.tools.join(' · ') : 'it advertises no tools'}
              </p>
            </>
          ) : (
            <p className="flex items-start gap-1.5 text-foreground">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-block-ink" aria-hidden />
              <span>
                Did not answer. <span className="text-muted-foreground">{probe.detail}</span>
              </span>
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
