'use client'

import {
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Loader2,
  Network,
  PlugZap,
  Power,
  Trash2,
} from 'lucide-react'
import { useId, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { Input } from '@/components/primitives/input'
import { EmptyState } from '@/components/primitives/States'
import type { McpConsole, McpProbe, McpServerCreate, McpServerUpdate } from '@/lib/api/mcp'

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
 * it advertises, or the sentence explaining why it did not reply.
 *
 * **Disabling is not deleting, and it is not a softer revocation.** A disabled peer's
 * tools leave the agent's payload entirely, so a model cannot see them and cannot try
 * them — while the connection stays configured, which is what an operator wants at
 * 3am when a peer starts misbehaving.
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
  const [confirming, setConfirming] = useState<string | null>(null)
  const id = useId()

  const submit = () => {
    onCreate({ ...draft, serverId: draft.serverId.trim() })
    setDraft({ serverId: '', label: '', url: '', authHeader: 'Authorization', credential: '' })
  }

  return (
    <Card className="rounded-lg">
      <CardHeader title="Connections" eyebrow="External MCP servers" />
      <CardBody className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Declaring a server says where to look for tools. It grants nothing: every tool a
          peer advertises arrives at <span className="font-medium text-foreground">high</span>{' '}
          risk and callable by nobody until a platform admin admits it below.
        </p>

        {data.servers.length > 0 ? (
          <Table>
            <THead>
              <TH>Server</TH>
              <TH>Endpoint</TH>
              <TH>Credential</TH>
              <TH className="text-right">Tools</TH>
              <TH>State</TH>
              <TH>
                <span className="sr-only">Actions</span>
              </TH>
            </THead>
            <TBody>
              {data.servers.map((server) => (
                <TR key={server.serverId}>
                  <TD className="align-top">
                    <span className="inline-flex items-center gap-2">
                      <Network className="size-3.5 text-muted-foreground" aria-hidden />
                      <span className="text-sm text-foreground">{server.label}</span>
                    </span>
                    <p className="mt-0.5 font-mono text-[0.68rem] text-muted-foreground">
                      mcp__{server.serverId}__
                    </p>
                  </TD>
                  <TD className="max-w-[16rem] align-top">
                    <p className="truncate font-mono text-xs text-muted-foreground">
                      {server.url || 'in-process'}
                    </p>
                    <p className="mt-0.5 font-mono text-[0.68rem] text-muted-foreground">
                      {server.authHeader}
                    </p>
                  </TD>
                  <TD className="align-top">
                    {server.hasCredential ? (
                      <>
                        <Badge tone="ok" className="gap-1">
                          <KeyRound className="size-3" aria-hidden />
                          set
                        </Badge>
                        <p className="mt-1 font-mono text-[0.68rem] text-muted-foreground">
                          {server.credentialFingerprint}
                        </p>
                        {server.credentialSetBy ? (
                          <p className="text-[0.68rem] text-muted-foreground">
                            by {server.credentialSetBy}
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <Badge tone="neutral" className="gap-1">
                          <KeyRound className="size-3" aria-hidden />
                          none
                        </Badge>
                        {server.credentialFingerprint ? (
                          <p className="mt-1 max-w-[11rem] text-[0.68rem] text-muted-foreground">
                            One was set before this process started. Aegis never writes a
                            peer&apos;s secret to its database — re-enter it, or set{' '}
                            <span className="font-mono">
                              AEGIS_MCP_CRED_
                              {server.serverId.toUpperCase().replace(/-/g, '_')}
                            </span>
                            .
                          </p>
                        ) : null}
                      </>
                    )}
                  </TD>
                  <TD className="align-top text-right">
                    <Figure className="text-foreground">{server.discoveredTools}</Figure>
                    <span className="text-muted-foreground" aria-hidden>
                      {' / '}
                    </span>
                    <Figure className="text-muted-foreground">{server.grantedTools}</Figure>
                    <p className="text-[0.68rem] text-muted-foreground">found / callable</p>
                  </TD>
                  <TD className="align-top">
                    {server.enabled ? (
                      <Badge tone="ok" className="gap-1">
                        <CheckCircle2 className="size-3" aria-hidden />
                        enabled
                      </Badge>
                    ) : (
                      <>
                        <Badge tone="risk" className="gap-1">
                          <Power className="size-3" aria-hidden />
                          disabled
                        </Badge>
                        <p className="mt-1 max-w-[11rem] text-[0.68rem] text-muted-foreground">
                          Its tools are not offered to any agent.
                        </p>
                      </>
                    )}
                  </TD>
                  <TD className="align-top">
                    <div className="flex flex-col items-end gap-1.5">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy === server.serverId}
                        onClick={() => onTest(server.serverId)}
                      >
                        {busy === server.serverId ? (
                          <Loader2 className="mr-1 size-3 animate-spin motion-reduce:animate-none" aria-hidden />
                        ) : (
                          <PlugZap className="mr-1 size-3" aria-hidden />
                        )}
                        Test
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={busy === server.serverId}
                        onClick={() =>
                          onUpdate(server.serverId, { enabled: !server.enabled })
                        }
                      >
                        {server.enabled ? 'Disable' : 'Enable'}
                      </Button>
                      {confirming === server.serverId ? (
                        <div className="flex flex-col items-end gap-1">
                          <p
                            id={`${id}-remove-${server.serverId}`}
                            className="max-w-[12rem] text-right text-[0.68rem] leading-relaxed text-block-ink"
                          >
                            Removing {server.label} forgets its endpoint, every tool it
                            advertised, every grant on those tools and its credential. Nothing
                            here can be undone from this page.
                          </p>
                          <div className="flex gap-1">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={() => setConfirming(null)}
                            >
                              Cancel
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="destructive"
                              aria-describedby={`${id}-remove-${server.serverId}`}
                              onClick={() => {
                                setConfirming(null)
                                onDelete(server.serverId)
                              }}
                            >
                              Remove {server.label}
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          onClick={() => setConfirming(server.serverId)}
                        >
                          <Trash2 className="mr-1 size-3" aria-hidden />
                          Remove
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        ) : (
          <EmptyState
            icon={Network}
            title="No external MCP server is declared"
            body="Every peer whose tools an agent here may reach is listed on this table. Declare one with the form below, or set AEGIS_MCP_CLIENT_SERVERS to a comma-separated id=url list before the process starts."
          />
        )}

        {probe ? (
          <div role="status" aria-live="polite" className="rounded-lg border border-border bg-surface-2 p-3">
            {probe.reachable ? (
              <>
                <p className="flex items-center gap-2 text-sm text-foreground">
                  <CheckCircle2 className="size-4 shrink-0 text-ok-ink" aria-hidden />
                  <span>
                    <span className="font-mono">{probe.serverId}</span> answered
                    {probe.serverName ? ` as ${probe.serverName}` : ''}
                    {probe.protocolVersion ? ` on protocol ${probe.protocolVersion}` : ''}.
                  </span>
                </p>
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  {probe.tools.length > 0
                    ? probe.tools.join(' · ')
                    : 'it advertises no tools'}
                </p>
              </>
            ) : (
              <p className="flex items-start gap-2 text-sm text-foreground">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
                <span>
                  <span className="font-mono">{probe.serverId}</span> did not answer.{' '}
                  <span className="text-muted-foreground">{probe.detail}</span>
                </span>
              </p>
            )}
          </div>
        ) : null}

        <form
          className="grid gap-3 border-t border-border pt-4 md:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <label className="flex flex-col gap-1" htmlFor={`${id}-server-id`}>
            <span className="text-sm font-medium text-foreground">Id</span>
            <Input
              id={`${id}-server-id`}
              value={draft.serverId}
              onChange={(event) => setDraft({ ...draft, serverId: event.target.value })}
              placeholder="acme…"
              autoComplete="off"
              spellCheck={false}
              required
            />
            <span className="text-[0.68rem] text-muted-foreground">
              Lowercase letters, digits and hyphens. It becomes the tool namespace{' '}
              <span className="font-mono">
                mcp__{draft.serverId.trim() || 'id'}__&lt;tool&gt;
              </span>
              , which is what stops a peer shadowing an Aegis tool.
            </span>
          </label>
          <label className="flex flex-col gap-1" htmlFor={`${id}-server-label`}>
            <span className="text-sm font-medium text-foreground">Label</span>
            <Input
              id={`${id}-server-label`}
              value={draft.label}
              onChange={(event) => setDraft({ ...draft, label: event.target.value })}
              placeholder="Acme tools…"
              autoComplete="off"
            />
          </label>
          <label className="flex flex-col gap-1" htmlFor={`${id}-server-url`}>
            <span className="text-sm font-medium text-foreground">Endpoint</span>
            <Input
              id={`${id}-server-url`}
              value={draft.url}
              onChange={(event) => setDraft({ ...draft, url: event.target.value })}
              placeholder="https://acme.example/mcp…"
              autoComplete="off"
              spellCheck={false}
              inputMode="url"
            />
            <span className="text-[0.68rem] text-muted-foreground">
              The peer&apos;s Streamable HTTP address.
            </span>
          </label>
          <label className="flex flex-col gap-1" htmlFor={`${id}-server-auth`}>
            <span className="text-sm font-medium text-foreground">Auth header</span>
            <Input
              id={`${id}-server-auth`}
              value={draft.authHeader}
              onChange={(event) => setDraft({ ...draft, authHeader: event.target.value })}
              placeholder="Authorization…"
              autoComplete="off"
              spellCheck={false}
            />
            <span className="text-[0.68rem] text-muted-foreground">
              Where the credential goes — <span className="font-mono">Authorization</span>{' '}
              for a bearer, <span className="font-mono">X-API-Key</span> for many others.
            </span>
          </label>
          <label className="flex flex-col gap-1 md:col-span-2" htmlFor={`${id}-server-cred`}>
            <span className="text-sm font-medium text-foreground">Credential</span>
            <Input
              id={`${id}-server-cred`}
              type="password"
              autoComplete="off"
              value={draft.credential}
              onChange={(event) => setDraft({ ...draft, credential: event.target.value })}
              placeholder="paste the peer’s secret…"
            />
            <span className="text-[0.68rem] text-muted-foreground">
              Held by the serving process and nowhere else. Aegis never writes a third
              party&apos;s secret to its own database, so there is nothing to read back —
              not through this API, and not out of a database dump. What is stored is a
              fingerprint. After a restart, re-enter it or supply it as{' '}
              <span className="font-mono">AEGIS_MCP_CRED_&lt;ID&gt;</span>.
            </span>
          </label>
          <div className="md:col-span-2">
            <Button type="submit" disabled={busy === 'create' || !draft.serverId.trim()}>
              {busy === 'create' ? (
                <Loader2 className="mr-2 size-4 animate-spin motion-reduce:animate-none" aria-hidden />
              ) : null}
              Declare server
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  )
}
