'use client'

import { AlertTriangle, Check, Loader2, Lock, PlugZap } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import {
  canWeaken,
  controlLabel,
  draftOf,
  fieldFor,
  formatValue,
  groupSettings,
  mergeNote,
  parseValue,
  provenanceOf,
  refusalSentence,
  writableScopes,
  writeOutcome,
  type SettingField as FieldShape,
  type WriteOutcome,
} from '@/components/settings/settingsCatalogue'
import {
  getSetting,
  getSettings,
  putSetting,
  type SettingRow,
  type SettingScope,
} from '@/lib/api/console'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Every settings screen, generated from the catalogue.
 *
 * There is no list of keys in this file and no branch on one. `GET /settings` returns
 * every control this caller may read — the catalogue's own descriptor per key, already
 * resolved — and this component draws whatever arrives: sections from the key
 * namespaces, the control from `control`/`type`, the help text from `description`, the
 * legal values from `choices`/`minimum`/`maximum`. A key added to `SETTING_SPECS` next
 * month appears here with nothing in `web/` edited, which is the entire mechanism
 * behind "operating this platform never requires touching code".
 *
 * What the screen refuses to hide, because each was a real defect:
 *
 * - **A control that binds to nothing.** `control.effective === false` renders as a
 *   value and the catalogue's `inert_reason`, never as an input. `agent.mode` says so
 *   today; `agent.gate_min_risk` did not, for a phase, and an operator changed a value
 *   that reached no run.
 * - **A control that is not theirs.** `writable_by` excludes them ⇒ the value and the
 *   sentence saying who may change it, not a greyed-out box that posts and 403s.
 * - **Where the value came from, and whether it can be weakened.** A `tighten_only` key
 *   and an `override` key look identical until one of them refuses a write.
 * - **A write that did not become the value in force.** The row re-renders from the
 *   PUT **response**, and {@link writeOutcome} says so in a sentence when the fold
 *   decided something other than what was typed.
 *
 * Every decision above lives in `settingsCatalogue.ts`, which has no React in it and is
 * tested directly.
 */

/** Shared input chrome — light theme, visible focus, no motion assumptions. */
const INPUT =
  'w-full rounded-lg border border-border bg-card px-2.5 py-1.5 text-sm text-foreground ' +
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

/** One control: what is in force, who decided it, and the way to change it. */
function SettingField({
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
  const [draft, setDraft] = useState<string>(() => draftOf(row.value))
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<WriteOutcome | null>(null)
  const field = fieldFor(row)
  const provenance = provenanceOf(row)
  const describedBy = `${row.key}-help`

  const save = useCallback(
    (raw: string) => {
      const submitted = parseValue(raw, row.control)
      setSaving(true)
      setFailure(null)
      setOutcome(null)
      putSetting(token, row.key, submitted, scope)
        .then((next) => {
          // The response, never the request: `strictest()` and the union fold both mean
          // the value now in force may not be the one that was typed, and the operator
          // has to see the one that is.
          onWritten(next)
          setDraft(draftOf(next.value))
          setOutcome(writeOutcome(submitted, scope, next))
        })
        .catch((error: unknown) => {
          setFailure(refusalSentence(error))
          // A refusal usually quotes what is already in force, and the commonest cause
          // of one is that an enclosing scope moved since the page loaded. Re-read THIS
          // key so the value beside the reason agrees with it.
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

  return (
    <div className="grid gap-4 py-5 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-medium text-foreground">{controlLabel(row.key)}</h4>
          <Badge tone={provenance.tone}>{provenance.label}</Badge>
          {field.kind === 'inert' ? (
            <Badge tone="risk">
              <PlugZap aria-hidden className="size-3" />
              Not wired up
            </Badge>
          ) : null}
          {field.kind === 'readOnly' ? (
            <Badge tone="neutral">
              <Lock aria-hidden className="size-3" />
              Read only
            </Badge>
          ) : null}
          {canWeaken(row.control) ? null : <Badge tone="neutral">Cannot be weakened</Badge>}
        </div>
        <p className="mt-1 font-mono text-[0.7rem] text-muted-foreground">{row.key}</p>
        <p id={describedBy} className="mt-2 max-w-prose text-[0.8rem] leading-relaxed text-muted-foreground">
          {row.control.description}
        </p>
        <p className="mt-2 max-w-prose text-[0.72rem] leading-relaxed text-muted-foreground">
          {mergeNote(row.control)} {provenance.detail}
        </p>
        {field.kind === 'inert' ? (
          <p className="mt-2 max-w-prose rounded-lg bg-surface-2 px-3 py-2 text-[0.72rem] leading-relaxed text-foreground">
            {field.reason}
          </p>
        ) : null}
      </div>

      <div className="min-w-0">
        <p className="text-[0.68rem] uppercase tracking-wide text-muted-foreground">In force</p>
        <p className="mt-0.5 font-mono text-sm break-words text-foreground">{formatValue(row.value)}</p>
        <div className="mt-2">
          <Editor
            row={row}
            field={field}
            draft={draft}
            saving={saving}
            describedBy={describedBy}
            onDraft={setDraft}
            onSave={save}
          />
        </div>
        {failure === null ? null : (
          <p className="mt-2 flex items-start gap-1.5 text-[0.72rem] leading-snug text-danger">
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>{failure}</span>
          </p>
        )}
        {outcome === null ? null : (
          <p
            className={`mt-2 flex items-start gap-1.5 text-[0.72rem] leading-snug ${
              outcome.took ? 'text-muted-foreground' : 'text-foreground'
            }`}
          >
            {outcome.took ? (
              <Check aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            ) : (
              <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            )}
            <span>{outcome.sentence}</span>
          </p>
        )}
      </div>
    </div>
  )
}

/** The input for one field — or the sentence that stands in for one. */
function Editor({
  row,
  field,
  draft,
  saving,
  describedBy,
  onDraft,
  onSave,
}: {
  row: SettingRow
  field: FieldShape
  draft: string
  saving: boolean
  describedBy: string
  onDraft: (raw: string) => void
  onSave: (raw: string) => void
}): ReactElement {
  const label = controlLabel(row.key)

  // Neither of these is an input, and that is the point of both: an inert key would
  // accept a write that reaches nothing, and an unwritable one would post and 403.
  if (field.kind === 'inert' || field.kind === 'readOnly') {
    return <p className="text-[0.74rem] leading-snug text-muted-foreground">{field.reason}</p>
  }

  if (field.kind === 'select') {
    return (
      <select
        aria-label={label}
        aria-describedby={describedBy}
        className={INPUT}
        value={draft}
        disabled={saving}
        onChange={(event) => {
          onDraft(event.target.value)
          onSave(event.target.value)
        }}
      >
        {field.choices.map((choice) => (
          <option key={String(choice)} value={String(choice)}>
            {String(choice)}
          </option>
        ))}
      </select>
    )
  }

  if (field.kind === 'toggle') {
    const on = draft === 'true'
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        aria-describedby={describedBy}
        disabled={saving}
        onClick={() => {
          const next = on ? 'false' : 'true'
          onDraft(next)
          onSave(next)
        }}
        className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span
          aria-hidden
          className={`inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors ${
            on ? 'bg-ok justify-end' : 'bg-surface-2 justify-start'
          }`}
        >
          <span className="size-3 rounded-full bg-card shadow-card" />
        </span>
        {on ? 'On' : 'Off'}
      </button>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <input
        aria-label={label}
        aria-describedby={describedBy}
        type={field.kind === 'number' ? 'number' : 'text'}
        min={field.kind === 'number' ? field.minimum : undefined}
        max={field.kind === 'number' ? field.maximum : undefined}
        step={field.kind === 'number' ? field.step : undefined}
        placeholder={field.kind === 'tags' ? 'comma separated' : undefined}
        className={INPUT}
        value={draft}
        disabled={saving}
        onChange={(event) => onDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onSave(draft)
        }}
      />
      <button
        type="button"
        disabled={saving}
        onClick={() => onSave(draft)}
        className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-60"
      >
        {saving ? <Loader2 aria-label="Saving" className="size-3.5 animate-spin" /> : 'Save'}
      </button>
    </div>
  )
}

/**
 * The whole settings surface for one signed-in principal.
 *
 * @param onWritten - Called with each accepted write's re-resolved row, so a caller can
 *   refresh anything downstream of it. The tool roster is one: `agent.gate_min_risk` is
 *   literally the gate floor it prints.
 */
export function SettingsForm({
  onWritten,
}: {
  onWritten?: (row: SettingRow) => void
}): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [rows, setRows] = useState<SettingRow[] | null>(null)
  const [tenantId, setTenantId] = useState<number | null>(null)
  const [userId, setUserId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [scope, setScope] = useState<SettingScope>('user')

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getSettings(token)
      .then((data) => {
        if (!alive) return
        setRows(data.rows)
        // The ids come off the token, server-side. Which layers this caller may write
        // at follows from them, so nothing here has to guess at the session's shape.
        setTenantId(data.tenant_id)
        setUserId(data.user_id)
        setError(null)
      })
      .catch((failure: unknown) => {
        if (alive) setError(refusalSentence(failure))
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const replace = useCallback(
    (next: SettingRow) => {
      setRows((current) =>
        current === null ? current : current.map((row) => (row.key === next.key ? next : row)),
      )
      onWritten?.(next)
    },
    [onWritten],
  )

  const scopes = useMemo(
    () => writableScopes(session?.fineRole, tenantId, userId),
    [session?.fineRole, tenantId, userId],
  )
  const chosen = scopes.find((option) => option.id === scope)

  // Land on a layer this caller can actually reach, rather than defaulting to `user`
  // and refusing every write for a platform principal that has no user row.
  useEffect(() => {
    if (chosen?.available === true) return
    const first = scopes.find((option) => option.available)
    if (first !== undefined) setScope(first.id)
  }, [chosen, scopes])

  const sections = useMemo(() => (rows === null ? [] : groupSettings(rows)), [rows])

  if (error !== null) {
    return (
      <Card>
        <CardBody>
          <p className="py-6 text-center text-sm text-danger">{error}</p>
        </CardBody>
      </Card>
    )
  }
  if (rows === null) {
    return (
      <Card>
        <CardBody>
          <p className="py-6 text-center text-sm text-muted-foreground">Reading the catalogue…</p>
        </CardBody>
      </Card>
    )
  }
  if (rows.length === 0) {
    return (
      <Card>
        <CardBody>
          <p className="py-6 text-center text-sm text-muted-foreground">
            Your role may read no controls in this catalogue.
          </p>
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardBody className="flex flex-wrap items-center justify-between gap-3">
          <label className="flex flex-wrap items-center gap-2 text-sm text-foreground">
            Changes apply to
            <select
              aria-label="Who a change applies to"
              className="rounded-lg border border-border bg-card px-2.5 py-1.5 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)]"
              value={scope}
              onChange={(event) => setScope(event.target.value as SettingScope)}
            >
              {scopes.map((option) => (
                <option key={option.id} value={option.id} disabled={!option.available}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <p className="max-w-prose text-[0.74rem] leading-snug text-muted-foreground">
            {scopes
              .filter((option) => !option.available)
              .map((option) => option.reason)
              .join(' ')}
          </p>
        </CardBody>
      </Card>

      {sections.map((section) => (
        <Card key={section.id}>
          <CardHeader
            title={section.title}
            eyebrow={`${section.rows.length} ${section.rows.length === 1 ? 'control' : 'controls'} · platform → tenant → you`}
          />
          <CardBody className="divide-y divide-border">
            {section.rows.map((row) => (
              <SettingField
                key={row.key}
                row={row}
                scope={scope}
                token={token}
                onWritten={replace}
              />
            ))}
          </CardBody>
        </Card>
      ))}
    </div>
  )
}
