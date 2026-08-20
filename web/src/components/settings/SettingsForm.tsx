'use client'

import {
  AlertTriangle,
  Bot,
  Check,
  Database,
  IdCard,
  Loader2,
  Lock,
  PlugZap,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
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
import { cn } from '@/lib/utils'

/**
 * Every settings screen, generated from the catalogue.
 *
 * There is no list of keys in this file and no branch on one. `GET /settings` returns
 * every control this caller may read — the catalogue's own descriptor per key, already
 * resolved — and this component draws whatever arrives: sections from the key
 * namespaces, the control from `control`/`type`, the help text from `description`, the
 * legal values from `choices`/`minimum`/`maximum`. A key added to `SETTING_SPECS` next
 * month appears here with nothing in `web/` edited, which is the entire mechanism
 * behind "operating this platform never requires touching code". Nothing below is
 * styled per key, and nothing below may be.
 *
 * **The layout is a rail and a panel**, not six stacked cards. Twenty-five controls,
 * each carrying a description, a merge note, a provenance receipt and a control, is a
 * page a reader scrolls rather than uses — and it was the screen the phone test called a
 * text hell. One category is open at a time; the rail says how many controls each holds
 * and how many of them are inert or read-only, so what is *not* on screen is still
 * counted. Nothing is hidden that was not already below the fold.
 *
 * What the screen refuses to hide, because each was a real defect:
 *
 * - **A control that binds to nothing.** `control.effective === false` renders as a
 *   value and the catalogue's `inert_reason`, never as an input. `agent.mode` says so
 *   today; `agent.gate_min_risk` did not, for a phase, and an operator changed a value
 *   that reached no run. The row is drawn so it cannot be mistaken for a live one: a
 *   muted surface, a marked edge, and — where a live row puts a control — the reason,
 *   verbatim, in a bordered block that reads as a statement rather than as a control
 *   somebody greyed out.
 * - **A control that is not theirs.** `writable_by` excludes them ⇒ the value and the
 *   sentence saying who may change it, not a greyed-out box that posts and 403s.
 * - **Where the value came from, and whether it can be weakened.** A `tighten_only` key
 *   and an `override` key look identical until one of them refuses a write. The
 *   deciding scope is a {@link Receipt} — the same provenance line the rest of the
 *   console uses, so a reader learns it once.
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

/** The one focus treatment on this screen: the ring token, at 2px, always visible. */
const FOCUS = 'outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * An icon per key **namespace**, for the category rail.
 *
 * Keyed on the namespace and never on the key, for exactly the reason
 * `SECTION_TITLES` is: this is a hint about a *group*, and a namespace nobody
 * anticipated still gets a rail entry — with the fallback glyph — rather than
 * disappearing. A key→icon table would be the hand-written second catalogue the
 * generated form exists to prevent.
 */
const SECTION_ICONS: Readonly<Record<string, LucideIcon>> = {
  agent: Bot,
  guardrails: ShieldCheck,
  jobs: Database,
  memory: Sparkles,
  seat: IdCard,
  skills: Sparkles,
}

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
  const inert = field.kind === 'inert'
  const nameId = `${row.key}-name`
  const describedBy = `${row.key}-help`
  const controlId = `${row.key}-control`

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
    <div
      className={cn(
        'grid gap-3 py-4 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_18rem]',
        // An inert row is not a styled special case of a live row — it is drawn on a
        // different surface, with a marked edge, so it never reads as one control
        // among the working ones.
        inert && 'md:-mx-6 -mx-5 border-l-2 border-risk bg-surface-2/60 px-5 md:px-6',
      )}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h4 id={nameId} className="flex items-center gap-1 text-sm font-medium text-foreground">
            {controlLabel(row.key)}
            {/* The catalogue's own sentence for this control. It was a paragraph under
                every one of twenty-five rows, which is what made this screen read as an
                essay; DESIGN.md §4 puts a paragraph that explains a mechanism in a tip.
                The same text stays in the DOM as the row's `aria-describedby` target
                below, so nothing is lost to a screen reader or to the search box. */}
            <InfoTip label={`What ${row.key} controls`}>{row.control.description}</InfoTip>
          </h4>
          <Badge tone={provenance.tone}>{provenance.label}</Badge>
          {inert ? (
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
          {canWeaken(row.control) ? null : (
            <Badge tone="neutral">
              Cannot be weakened
              {/* The merge rule is part of the value — a `tighten_only` key and an
                  `override` key behave in opposite ways when a tenant writes them. It
                  was a second paragraph on every row; it is the badge's own tip now. */}
              <InfoTip label={`How ${row.key} merges across scopes`}>
                {mergeNote(row.control)}
              </InfoTip>
            </Badge>
          )}
        </div>
        <p id={describedBy} className="sr-only">
          {row.control.description}
        </p>
        {/* The receipt names the deciding scope; the *sentence* explaining what that
            scope means was three more lines on every one of twenty-five rows, so it
            moved one layer down (DESIGN.md §4). Nothing is lost — the provenance is
            still on the row, in the one treatment the console uses everywhere. */}
        <span className="mt-1.5 flex flex-wrap items-center gap-1">
          <Receipt
            label="Decided by"
            origin={`${provenance.label.toLowerCase()} · ${row.key}`}
            variant="inline"
          />
          <InfoTip label={`What decided ${row.key}`}>{provenance.detail}</InfoTip>
        </span>
      </div>

      <div className="min-w-0">
        <p className="eyebrow">In force</p>
        <Figure
          className={cn('mt-0.5 break-words', inert ? 'text-muted-foreground' : 'text-foreground')}
        >
          {formatValue(row.value)}
        </Figure>
        <div className="mt-2">
          <Editor
            row={row}
            field={field}
            draft={draft}
            saving={saving}
            controlId={controlId}
            nameId={nameId}
            describedBy={describedBy}
            onDraft={setDraft}
            onSave={save}
          />
        </div>
        {failure === null ? null : (
          <p
            role="alert"
            className="mt-2 flex items-start gap-1.5 text-[0.72rem] leading-snug text-danger"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>{failure}</span>
          </p>
        )}
        {outcome === null ? null : (
          <p
            role="status"
            className={cn(
              'mt-2 flex items-start gap-1.5 text-[0.72rem] leading-snug',
              outcome.took ? 'text-muted-foreground' : 'text-foreground',
            )}
          >
            {outcome.took ? (
              <Check aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            ) : (
              <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-risk-ink" />
            )}
            <span>{outcome.sentence}</span>
          </p>
        )}
      </div>
    </div>
  )
}

/**
 * The input for one field — or the statement that stands in for one.
 *
 * Neither refusal is an input, and that is the point of both: an inert key would accept
 * a write that reaches nothing, and an unwritable one would post and 403. They are
 * drawn as bordered statements rather than as disabled controls, because a disabled
 * control invites a reader to look for the permission that would enable it, and for an
 * inert key there is none — nothing reads the value at all.
 */
function Editor({
  row,
  field,
  draft,
  saving,
  controlId,
  nameId,
  describedBy,
  onDraft,
  onSave,
}: {
  row: SettingRow
  field: FieldShape
  draft: string
  saving: boolean
  controlId: string
  nameId: string
  describedBy: string
  onDraft: (raw: string) => void
  onSave: (raw: string) => void
}): ReactElement {
  const label = controlLabel(row.key)

  if (field.kind === 'inert' || field.kind === 'readOnly') {
    const Icon = field.kind === 'inert' ? PlugZap : Lock
    return (
      // Two lines, then a tip. The refusal is the server's own sentence and is never
      // paraphrased, but four of them stacked down a category turned a control panel
      // into a page of apologies — so the first clause is on the page and the rest is
      // one layer down, in the tip beside it.
      <p className="flex items-start gap-2 rounded-lg border border-border bg-card px-3 py-2.5 text-[0.74rem] leading-relaxed text-foreground">
        <Icon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <span className="line-clamp-2 min-w-0">{field.reason}</span>
        <InfoTip
          className="mt-0.5 shrink-0"
          label={field.kind === 'inert' ? 'Why this control does nothing' : 'Why this is read only'}
        >
          {field.reason}
        </InfoTip>
      </p>
    )
  }

  if (field.kind === 'select') {
    return (
      <>
        <label htmlFor={controlId} className="sr-only">
          {label}
        </label>
        <select
          id={controlId}
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
      </>
    )
  }

  if (field.kind === 'toggle') {
    const on = draft === 'true'
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-labelledby={nameId}
        aria-describedby={describedBy}
        disabled={saving}
        onClick={() => {
          const next = on ? 'false' : 'true'
          onDraft(next)
          onSave(next)
        }}
        className="inline-flex touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span
          aria-hidden
          className={cn(
            'inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors',
            // Blue, not green: on/off is not a health verdict, and the reserved status
            // hues are not spent on a switch (DESIGN.md §2).
            on ? 'justify-end bg-primary' : 'justify-start bg-input',
          )}
        >
          <span className="size-3 rounded-full bg-card" />
        </span>
        {on ? 'On' : 'Off'}
      </button>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={controlId} className="sr-only">
        {label}
      </label>
      <input
        id={controlId}
        aria-describedby={describedBy}
        type={field.kind === 'number' ? 'number' : 'text'}
        min={field.kind === 'number' ? field.minimum : undefined}
        max={field.kind === 'number' ? field.maximum : undefined}
        step={field.kind === 'number' ? field.step : undefined}
        placeholder={field.kind === 'tags' ? 'comma separated, e.g. EMAIL, PHONE…' : undefined}
        autoComplete="off"
        spellCheck={false}
        className={cn(INPUT, 'tabular font-mono')}
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
        className="shrink-0 touch-manipulation rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-60"
      >
        {saving ? (
          <Loader2 aria-hidden className="size-3.5 animate-spin motion-reduce:animate-none" />
        ) : (
          'Save'
        )}
        <span className="sr-only">{saving ? `Saving ${label}` : `Save ${label}`}</span>
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
  const [open, setOpen] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

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

  // A search crosses every category, so it is not a filter *inside* one — it is its own
  // view. Selecting a category clears it, and typing clears the category.
  const needle = filter.trim().toLowerCase()
  const found = useMemo(
    () =>
      needle === '' || rows === null
        ? []
        : rows.filter(
            (row) =>
              row.key.toLowerCase().includes(needle) ||
              row.control.description.toLowerCase().includes(needle),
          ),
    [rows, needle],
  )

  // Open the first category once the catalogue arrives, rather than opening on nothing.
  useEffect(() => {
    if (open === null && sections.length > 0) setOpen(sections[0].id)
  }, [open, sections])

  if (error !== null) {
    return <ErrorState error={error} />
  }
  if (rows === null) {
    return <LoadingState rows={4} label="Reading the catalogue…" />
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={SlidersHorizontal}
        title="No controls you may read"
        body="Every setting on this platform is declared in one catalogue, and each declares which roles may read it. Your role is on none of them. An administrator can widen that."
      />
    )
  }

  const active = sections.find((section) => section.id === open) ?? sections[0]
  const unavailable = scopes.filter((option) => !option.available)

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
      {/* The rail: every category, always countable, one open at a time. */}
      <div className="flex flex-col gap-4">
        <Card>
          <CardBody className="flex flex-col gap-3">
            <div>
              <label
                htmlFor="settings-scope"
                className="eyebrow mb-1 flex items-center gap-1"
              >
                Changes apply to
                {unavailable.length > 0 ? (
                  <InfoTip label="Why a layer is unavailable">
                    {unavailable.map((option) => option.reason).join(' ')}
                  </InfoTip>
                ) : null}
              </label>
              <select
                id="settings-scope"
                className={cn(INPUT, 'text-sm')}
                value={scope}
                onChange={(event) => setScope(event.target.value as SettingScope)}
              >
                {scopes.map((option) => (
                  <option key={option.id} value={option.id} disabled={!option.available}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="relative">
              <label htmlFor="settings-search" className="sr-only">
                Search every control
              </label>
              <Search
                aria-hidden
                className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground/60"
              />
              <input
                id="settings-search"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder="Search all controls…"
                autoComplete="off"
                spellCheck={false}
                className={cn(INPUT, 'h-8 pl-8 text-xs')}
              />
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader eyebrow="settings catalogue" title="Categories" as="h3" />
          <CardBody className="pt-0">
            <ul className="flex flex-col gap-0.5">
              {sections.map((section) => {
                const Icon = SECTION_ICONS[section.id] ?? SlidersHorizontal
                const on = needle === '' && active?.id === section.id
                const inert = section.rows.filter(
                  (row) => fieldFor(row).kind === 'inert',
                ).length
                const locked = section.rows.filter(
                  (row) => fieldFor(row).kind === 'readOnly',
                ).length
                return (
                  <li key={section.id}>
                    <button
                      type="button"
                      aria-current={on ? 'true' : undefined}
                      onClick={() => {
                        setOpen(section.id)
                        setFilter('')
                      }}
                      className={cn(
                        'flex w-full touch-manipulation items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors duration-[--dur-fast]',
                        FOCUS,
                        on
                          ? 'bg-blue-50 font-medium text-foreground'
                          : 'text-muted-foreground hover:bg-surface-2/70',
                      )}
                    >
                      <Icon
                        aria-hidden
                        className={cn('size-4 shrink-0', on ? 'text-blue-700' : '')}
                      />
                      <span className="min-w-0 flex-1 text-pretty leading-tight">{section.title}</span>
                      {inert > 0 ? (
                        <PlugZap
                          className="size-3 shrink-0 text-risk-ink"
                          aria-label={`${inert} not wired up`}
                        />
                      ) : null}
                      {locked > 0 ? (
                        <Lock
                          className="size-3 shrink-0 text-muted-foreground"
                          aria-label={`${locked} read only`}
                        />
                      ) : null}
                      <Figure className="shrink-0 text-[0.7rem] text-muted-foreground">
                        {section.rows.length}
                      </Figure>
                    </button>
                  </li>
                )
              })}
            </ul>
          </CardBody>
        </Card>
      </div>

      {/* The panel: one category, or every match for a search. */}
      <div className="min-w-0">
        {needle !== '' ? (
          <Card>
            <CardHeader
              eyebrow={`${found.length} ${found.length === 1 ? 'control' : 'controls'} match`}
              title={`Search “${filter.trim()}”`}
            />
            <CardBody className="divide-y divide-border pt-0">
              {found.length === 0 ? (
                <p className="py-4 text-sm text-muted-foreground italic">
                  No control&rsquo;s key or description contains that.
                </p>
              ) : (
                found.map((row) => (
                  <SettingField
                    key={row.key}
                    row={row}
                    scope={scope}
                    token={token}
                    onWritten={replace}
                  />
                ))
              )}
            </CardBody>
          </Card>
        ) : active ? (
          <Card>
            <CardHeader
              title={active.title}
              eyebrow={`${active.rows.length} ${active.rows.length === 1 ? 'control' : 'controls'} · ${active.id}.*`}
              actions={
                <InfoTip label="How a value is decided">
                  Every value is resolved platform → tenant → you. A `tighten_only` key folds
                  to the strictest of the three, so a write can be accepted and still not be
                  the value in force; an `override` key takes the innermost scope that set
                  it. Each row names which scope decided it.
                </InfoTip>
              }
            />
            <CardBody className="divide-y divide-border pt-0">
              {active.rows.map((row) => (
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
        ) : null}
      </div>
    </div>
  )
}
