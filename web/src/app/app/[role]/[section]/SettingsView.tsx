'use client'

import { Loader2, ShieldCheck, SlidersHorizontal, Wrench } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import {
  ConsoleApiError,
  getSetting,
  getSettings,
  getToolRoster,
  putSetting,
  type SettingRow,
  type SettingScope,
  type ToolRosterResponse,
} from '@/lib/api/console'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Settings — the per-tenant control plane, and the one screen that says *who decided*.
 *
 * Two panels, and each exists because the value alone is not enough to act on:
 *
 * - **Controls** renders `GET /settings`: every catalogue key this role may read, its
 *   effective value, and the scope that decided it. "Team (your setting)" and "Team
 *   (your tenant's default)" show the same word and mean opposite things — the badge is
 *   the difference between a control somebody trusts and one they poke at. Writing goes
 *   through `PUT /settings/{key}`, and the row re-renders from the **response**, so a
 *   write that lost to a stricter enclosing scope shows the value actually in force
 *   rather than the one that was typed.
 * - **Tools** renders `GET /tools`: the effective roster for the caller's persona, with
 *   the layer that constrains each tool. Read-only, deliberately — pinning a subset for
 *   one run needs a per-run field the query request does not carry, and a pin control
 *   that changed nothing would be the exact defect this screen exists to remove.
 *
 * Nothing here re-implements a rule. The legal values, the bounds and the help text
 * come from the catalogue's own descriptor; every refusal is the resolver's, shown with
 * its reason instead of a status line.
 */

/** How a source scope is labelled and toned. */
const SOURCE: Record<SettingRow['source'], { label: string; tone: BadgeTone }> = {
  platform: { label: 'Platform default', tone: 'neutral' },
  tenant: { label: "Your tenant's default", tone: 'graph' },
  user: { label: 'Your setting', tone: 'ok' },
}

/** How a tool's deciding layer is labelled and toned. */
const DECIDED_BY: Record<string, { label: string; tone: BadgeTone }> = {
  platform: { label: 'Available', tone: 'ok' },
  persona: { label: 'Not in your persona', tone: 'neutral' },
  tenant: { label: 'Human approval required', tone: 'risk' },
}

/** The scopes a write may target, in widening order. */
const SCOPES: Array<{ id: SettingScope; label: string }> = [
  { id: 'user', label: 'My preference' },
  { id: 'tenant', label: "My tenant's default" },
  { id: 'platform', label: 'Platform default' },
]

/** Render a resolved value for display, whatever the catalogue declared its type as. */
function shown(value: unknown): string {
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.join(', ')
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return value == null ? '—' : String(value)
}

/**
 * Parse what a control produced back into the type the catalogue declared.
 *
 * Deliberately no clamping and no rounding: an out-of-range value is refused by the
 * server with a reason, which is more useful than silently becoming a different number.
 */
function parsed(raw: string, control: SettingRow['control']): unknown {
  if (control.type === 'bool') return raw === 'true'
  if (control.type === 'int') return Number.parseInt(raw, 10)
  if (control.type === 'float') return Number.parseFloat(raw)
  if (control.type === 'list') {
    return raw
      .split(',')
      .map((part) => part.trim())
      .filter((part) => part.length > 0)
  }
  return raw
}

/** One control: its value, its source badge, and the editor when the caller may write. */
function ControlRow({
  row,
  scope,
  token,
  onWritten,
}: {
  row: SettingRow
  scope: SettingScope
  token: string | null
  onWritten: (next: SettingRow) => void
}): ReactElement {
  const [draft, setDraft] = useState<string>(() =>
    Array.isArray(row.value) ? row.value.join(', ') : String(row.value ?? ''),
  )
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const source = SOURCE[row.source]

  const save = useCallback(
    (raw: string) => {
      setSaving(true)
      setFailure(null)
      putSetting(token, row.key, parsed(raw, row.control), scope)
        .then((next) => {
          onWritten(next)
          setDraft(Array.isArray(next.value) ? next.value.join(', ') : String(next.value ?? ''))
        })
        .catch((error: unknown) => {
          setFailure(
            error instanceof ConsoleApiError ? error.message : 'The write could not be sent.',
          )
          // A refusal usually quotes what is already in force — "weaker than the 'medium'
          // already in force from the enclosing scope" — and the commonest cause of one
          // is that the enclosing scope moved since this page loaded. Re-read THIS key so
          // the "In force" column agrees with the reason beside it; re-fetching the whole
          // catalogue to explain one refusal would be a round trip for eleven rows nobody
          // asked about.
          getSetting(token, row.key)
            .then(onWritten)
            .catch(() => {
              /* the refusal is already on screen; a failed re-read adds nothing */
            })
        })
        .finally(() => setSaving(false))
    },
    [onWritten, row.control, row.key, scope, token],
  )

  const editor = (): ReactElement => {
    if (!row.writable) {
      return (
        <span className="text-sm text-muted-foreground">
          {shown(row.value)} · not writable by your role
        </span>
      )
    }
    if (row.control.control === 'select') {
      return (
        <select
          aria-label={row.key}
          className="w-full rounded-lg border border-border bg-card px-2 py-1.5 text-sm"
          value={draft}
          disabled={saving}
          onChange={(event) => {
            setDraft(event.target.value)
            save(event.target.value)
          }}
        >
          {(row.control.choices ?? []).map((choice) => (
            <option key={String(choice)} value={String(choice)}>
              {String(choice)}
            </option>
          ))}
        </select>
      )
    }
    if (row.control.type === 'bool') {
      return (
        <select
          aria-label={row.key}
          className="w-full rounded-lg border border-border bg-card px-2 py-1.5 text-sm"
          value={draft === 'true' || draft === 'True' ? 'true' : 'false'}
          disabled={saving}
          onChange={(event) => {
            setDraft(event.target.value)
            save(event.target.value)
          }}
        >
          <option value="false">off</option>
          <option value="true">on</option>
        </select>
      )
    }
    return (
      <div className="flex items-center gap-2">
        <input
          aria-label={row.key}
          type={row.control.control === 'number' ? 'number' : 'text'}
          min={row.control.minimum}
          max={row.control.maximum}
          className="w-full rounded-lg border border-border bg-card px-2 py-1.5 text-sm"
          value={draft}
          disabled={saving}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button
          type="button"
          disabled={saving}
          onClick={() => save(draft)}
          className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground hover:bg-surface-2 disabled:opacity-50"
        >
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : 'Save'}
        </button>
      </div>
    )
  }

  return (
    <tr className="border-t border-border align-top">
      <td className="py-3 pr-4">
        <p className="font-mono text-[0.78rem] text-foreground">{row.key}</p>
        <p className="mt-1 max-w-md text-[0.74rem] leading-snug text-muted-foreground">
          {row.control.description}
        </p>
      </td>
      <td className="py-3 pr-4 text-sm text-foreground">{shown(row.value)}</td>
      <td className="py-3 pr-4">
        <Badge tone={source.tone}>{source.label}</Badge>
        {row.control.merge === 'tighten_only' ? (
          <p className="mt-1 text-[0.7rem] text-muted-foreground">
            May only be tightened — a weaker write is refused.
          </p>
        ) : null}
      </td>
      <td className="w-64 py-3">
        {editor()}
        {failure === null ? null : (
          <p className="mt-1.5 text-[0.72rem] leading-snug text-danger">{failure}</p>
        )}
      </td>
    </tr>
  )
}

/** The settings + tools surface for one signed-in principal. */
function SettingsView(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [rows, setRows] = useState<SettingRow[] | null>(null)
  const [roster, setRoster] = useState<ToolRosterResponse | null>(null)
  const [scope, setScope] = useState<SettingScope>('user')
  const [error, setError] = useState<string | null>(null)
  const [rosterError, setRosterError] = useState<string | null>(null)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getSettings(token)
      .then((data) => {
        if (alive) {
          setRows(data.rows)
          setError(null)
        }
      })
      .catch((failure: unknown) => {
        if (alive) {
          setError(
            failure instanceof ConsoleApiError
              ? failure.message
              : 'Could not read the settings catalogue.',
          )
        }
      })
    getToolRoster(token)
      .then((data) => {
        if (alive) {
          setRoster(data)
          setRosterError(null)
        }
      })
      .catch((failure: unknown) => {
        if (alive) {
          setRosterError(
            failure instanceof ConsoleApiError
              ? failure.message
              : 'Could not read the tool roster.',
          )
        }
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const replace = useCallback((next: SettingRow) => {
    setRows((current) =>
      current === null ? current : current.map((row) => (row.key === next.key ? next : row)),
    )
  }, [])

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">platform → tenant → you · every value names who decided</p>
        <h1 className="t-hero text-foreground">Settings</h1>
      </div>

      <Card>
        <CardHeader
          title="Controls"
          eyebrow="resolved catalogue"
          actions={
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <SlidersHorizontal aria-hidden className="size-3.5" />
              Write at
              <select
                aria-label="Write at scope"
                className="rounded-lg border border-border bg-card px-2 py-1 text-xs text-foreground"
                value={scope}
                onChange={(event) => setScope(event.target.value as SettingScope)}
              >
                {SCOPES.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          }
        />
        <CardBody>
          {error !== null ? (
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          ) : rows === null ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Reading settings…</p>
          ) : rows.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Your role may read no controls in this catalogue.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                    <th className="pb-2 pr-4 font-medium">Control</th>
                    <th className="pb-2 pr-4 font-medium">In force</th>
                    <th className="pb-2 pr-4 font-medium">Decided by</th>
                    <th className="pb-2 font-medium">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <ControlRow
                      key={row.key}
                      row={row}
                      scope={scope}
                      token={token}
                      onWritten={replace}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Tools"
          eyebrow="platform ∩ persona, then the tenant's gate floor"
          actions={
            roster === null ? null : (
              <Badge tone="agent">
                <Wrench aria-hidden className="size-3" />
                {roster.allowed_count} of {roster.total} available
              </Badge>
            )
          }
        />
        <CardBody>
          {rosterError !== null ? (
            <p className="py-8 text-center text-sm text-danger">{rosterError}</p>
          ) : roster === null ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Reading the roster…</p>
          ) : (
            <>
              <p className="mb-3 flex items-center gap-1.5 text-[0.74rem] text-muted-foreground">
                <ShieldCheck aria-hidden className="size-3.5" />
                Persona <span className="font-mono text-foreground">{roster.persona}</span> · human
                gate at{' '}
                <span className="font-mono text-foreground">{roster.gate_min_risk}</span> risk and
                above
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                      <th className="pb-2 pr-4 font-medium">Tool</th>
                      <th className="pb-2 pr-4 font-medium">Risk</th>
                      <th className="pb-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roster.rows.map((tool) => {
                      const decided = DECIDED_BY[tool.decided_by] ?? {
                        label: tool.decided_by,
                        tone: 'neutral' as BadgeTone,
                      }
                      return (
                        <tr key={tool.name} className="border-t border-border align-top">
                          <td className="py-3 pr-4">
                            <p className="font-mono text-[0.78rem] text-foreground">{tool.name}</p>
                            <p className="mt-1 max-w-lg text-[0.74rem] leading-snug text-muted-foreground">
                              {tool.description}
                            </p>
                          </td>
                          <td className="py-3 pr-4 text-sm text-muted-foreground">{tool.risk}</td>
                          <td className="py-3">
                            <Badge tone={decided.tone}>{decided.label}</Badge>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/** Client entry for the Settings section — gated on a reachable backend. */
export function SettingsMount(): ReactElement {
  return (
    <BackendGate>
      <SettingsView />
    </BackendGate>
  )
}
