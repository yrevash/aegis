'use client'

import { CheckCircle2, Loader2, Lock, ShieldAlert, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Button } from '@/components/primitives/button'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import {
  activatePromptVersion,
  createPromptVersion,
  getPromptRun,
  getPromptRuns,
  getPromptScreen,
  llmopsErrorMessage,
  rollbackPrompt,
  type PromptRunRow,
  type PromptScreen,
  type PromptVersionRow,
} from '@/lib/api/llmops'
import { useAuth } from '@/lib/auth/AuthContext'

import { PROMPT_KEY } from './opsShared'
import { draftRefusal, mayReseed } from './promptDraft'

/**
 * When a run happened, in the reader's own locale — one formatter, built once,
 * rather than a `toLocaleString()` call per row.
 */
const RUN_TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/**
 * **Prompt control** — change the live system prompt without a deploy, and see that it
 * happened (§7.7).
 *
 * The rest of the LLMOps page watches the loop; this is the one panel an operator acts
 * on. Write a new version of the task prompt, make it live, roll it back, and read which
 * version each recent run was actually served.
 *
 * Two things on this screen are deliberately *not* editable, and both say so rather than
 * being quietly absent:
 *
 * - **The platform floor.** A version is the task half only. The safety preamble, the
 *   persona's data scope and its tool allowlist are composed underneath every version at
 *   render time, and no version can remove them. It is shown here so it can be read
 *   instead of discovered by experiment.
 * - **Another tenant's prompt.** The scope line names whose prompt this is. The server
 *   reads it from the caller's sealed session, so it is a statement of fact, not a
 *   filter this component chose.
 *
 * **Why the box can be empty, and why that is now said out loud.** `GET /llmops/prompts`
 * returns `activePrompt: null` for every scope that has no *promoted* version — the
 * `onShippedPrompt` case — because the prompt actually running there is the shipped
 * persona prompt, which lives in the adapter and is not on that wire. The editor used to
 * fold that null into `''`, so a box labelled "Task prompt" rendered blank while a prompt
 * was live, and an operator's first "edit" was in fact a **total replacement**: whatever
 * they typed became the tenant's entire task prompt. That is exactly how the defect was
 * found — a scope's prompt was replaced by one sentence. Two things follow:
 *
 * - The blank box is no longer presented as the live text. When there is no version to
 *   load, the panel says there is none and says what saving will do.
 * - The seed is re-applied when the live text changes underneath an untouched box (an
 *   activate, a rollback), and never over a box somebody has typed into.
 */
export function PromptControl(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [screen, setScreen] = useState<PromptScreen | null>(null)
  const [runs, setRuns] = useState<PromptRunRow[]>([])
  const [runWindow, setRunWindow] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [lookupId, setLookupId] = useState('')
  const [lookup, setLookup] = useState<string | null>(null)
  /** The version whose "Make live" is armed — at most one at a time. */
  const [confirmActivate, setConfirmActivate] = useState<number | null>(null)

  /**
   * What the box was last seeded with, and what is in it now.
   *
   * Refs rather than state because both are read inside `load()`, which runs after an
   * activate: reading `draft` from the closure there would compare against the value
   * captured when the callback was built. The pair is what lets the seed re-apply when
   * the live prompt moves without ever overwriting typing.
   */
  const seededFrom = useRef<string | null>(null)
  const draftRef = useRef('')

  /** Put text in the box and record that the box holds exactly that text. */
  const seed = useCallback((text: string | null): void => {
    seededFrom.current = text
    draftRef.current = text ?? ''
    setDraft(text ?? '')
  }, [])

  /** Type into the box — from here on it is the operator's, not the seed's. */
  const type = useCallback((text: string): void => {
    draftRef.current = text
    setDraft(text)
  }, [])

  const load = useCallback(async () => {
    if (!hydrated) return
    setLoading(true)
    setError(null)
    try {
      const next = await getPromptScreen(token, PROMPT_KEY)
      setScreen(next)
      if (mayReseed(draftRef.current, seededFrom.current)) seed(next.activePrompt)
    } catch (err) {
      setError(llmopsErrorMessage(err))
    } finally {
      setLoading(false)
    }
    try {
      const seen = await getPromptRuns(token)
      setRuns(seen.rows)
      setRunWindow(seen.window)
    } catch {
      // The attribution list is evidence, not the control: a failure here must not hide
      // the prompt the operator came to change.
      setRuns([])
    }
  }, [token, hydrated, seed])

  useEffect(() => {
    void load()
  }, [load])

  const act = async (fn: () => Promise<PromptScreen>, said: string): Promise<void> => {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      setScreen(await fn())
      setNote(said)
      void load()
    } catch (err) {
      setError(llmopsErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const saveDraft = async (): Promise<void> => {
    // A blank body is refused, not confirmed — `draftRefusal` carries the reason why.
    const refusal = draftRefusal(draft)
    if (refusal !== null) {
      setError(refusal)
      return
    }
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const row = await createPromptVersion(token, {
        promptKey: PROMPT_KEY,
        systemPrompt: draft,
      })
      setNote(`Saved version ${row.version} as a draft. It is not live until you activate it.`)
      await load()
    } catch (err) {
      setError(llmopsErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const editable = screen?.editable !== false
  const canRollback = (screen?.versions ?? []).some(
    (row) => row.status === 'archived' && row.activatedAt != null,
  )
  /** What making a version live would displace — named in the confirmation. */
  const replaces = screen?.onShippedPrompt
    ? 'the shipped prompt'
    : `version ${screen?.activeVersion}`

  return (
    <Card collapsible>
      <CardHeader
        eyebrow={screen ? screen.scopeLabel : 'prompt control'}
        title="Live prompt"
        actions={
          <InfoTip label="About prompt control">
            Activating a version takes effect on the next run with no deploy; the platform
            floor below it cannot be edited here.
          </InfoTip>
        }
      />
      <CardBody className="min-w-0">
        {loading ? (
          <LoadingState rows={5} label="Loading the live prompt…" />
        ) : error && screen == null ? (
          <ErrorState error={error} />
        ) : screen == null ? null : (
          <div className="min-w-0 space-y-6">
            {/* What is live, right now. */}
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-md bg-surface-2 px-2.5 py-1 text-sm text-foreground">
                <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
                {screen.onShippedPrompt
                  ? 'Running the shipped prompt'
                  : `Running version ${screen.activeVersion}`}
              </span>
              <span className="text-sm text-muted-foreground">{screen.scopeLabel}</span>
              {canRollback && editable && (
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-auto"
                  disabled={busy}
                  onClick={() =>
                    void act(
                      () => rollbackPrompt(token, PROMPT_KEY),
                      'Rolled back to the version that was live before.',
                    )
                  }
                >
                  <Undo2 className="size-4" aria-hidden="true" /> Roll back
                </Button>
              )}
            </div>

            {/* Write a version. */}
            <div>
              <label
                htmlFor="prompt-draft"
                className="mb-2 block text-sm font-medium text-foreground"
              >
                Task prompt
              </label>
              {/* The blank box, explained where it is blank. `activePrompt` is null for
                  every scope with no promoted version, so there is genuinely nothing to
                  load — and the box must not be read as "the live prompt is empty". */}
              {screen.activePrompt == null && (
                <p className="mb-2 text-sm break-words text-muted-foreground">
                  No version here yet. The shipped prompt is running and its text is not on
                  this API, so anything saved here replaces it in full.
                </p>
              )}
              <textarea
                id="prompt-draft"
                name="system_prompt"
                translate="no"
                value={draft}
                readOnly={!editable}
                onChange={(e) => type(e.target.value)}
                rows={8}
                className="w-full rounded-lg border border-border bg-card px-3 py-2 font-mono text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={busy || !editable || draftRefusal(draft) !== null}
                  onClick={() => void saveDraft()}
                >
                  {busy && (
                    <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
                  )}
                  {busy ? 'Saving…' : 'Save as a new version'}
                </Button>
                {/* A disabled button with no reason beside it is a control that refuses
                    silently. Six words is the whole explanation. */}
                {editable && draftRefusal(draft) !== null && (
                  <span className="text-sm text-muted-foreground">
                    A version needs a body.
                  </span>
                )}
                {!editable && (
                  <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                    <Lock className="size-4" aria-hidden="true" /> This prompt belongs to the
                    platform and is read-only here.
                  </span>
                )}
              </div>
            </div>

            {note && (
              <p role="status" aria-live="polite" className="text-sm break-words text-success">
                {note}
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm break-words text-danger">
                {error}
              </p>
            )}

            {/* History. */}
            <div>
              <h4 className="t-label mb-2 text-foreground">Versions</h4>
              {screen.versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No versions yet. Saving one puts it here as a draft.
                </p>
              ) : (
                <ul className="min-w-0 divide-y divide-border rounded-lg border border-border">
                  {screen.versions.map((row) => (
                    <VersionLine
                      key={row.id}
                      row={row}
                      busy={busy}
                      editable={editable}
                      scopeLabel={screen.scopeLabel}
                      replaces={replaces}
                      confirming={confirmActivate === row.id}
                      onArm={(on) => setConfirmActivate(on ? row.id : null)}
                      onActivate={() => {
                        setConfirmActivate(null)
                        void act(
                          () => activatePromptVersion(token, row.id),
                          `Version ${row.version} is live. Every run from now uses it.`,
                        )
                      }}
                      // Opening a version hands the box to the operator: a later reload
                      // must not pull it back to whatever is live.
                      onLoad={() => type(row.systemPrompt)}
                    />
                  ))}
                </ul>
              )}
            </div>

            {/* The floor — read, never edit. */}
            <div>
              <h4 className="t-label mb-2 text-foreground">
                Platform floor — composed underneath every version
              </h4>
              <pre
                translate="no"
                className="max-h-56 overflow-auto break-words whitespace-pre-wrap rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-xs text-muted-foreground"
              >
                {screen.floor}
              </pre>
            </div>

            {/* Which version each run used. */}
            <div>
              <h4 className="t-label mb-2 text-foreground">Recent runs</h4>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <label htmlFor="run-lookup" className="sr-only">
                  Run id
                </label>
                {/* A run id is an opaque identifier: no autocomplete history, no
                    spellcheck squiggle, no machine translation. */}
                <input
                  id="run-lookup"
                  name="run_id"
                  value={lookupId}
                  autoComplete="off"
                  spellCheck={false}
                  translate="no"
                  placeholder="Paste a run id…"
                  onChange={(e) => setLookupId(e.target.value)}
                  className="h-9 min-w-0 flex-1 rounded-md border border-border bg-card px-3 font-mono text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={lookupId.trim().length === 0}
                  onClick={() => {
                    void (async () => {
                      try {
                        const found = await getPromptRun(token, lookupId.trim())
                        setLookup(
                          found.version == null
                            ? `That run used the shipped prompt.`
                            : `That run used version ${found.version} of ${found.promptKey}.`,
                        )
                      } catch (err) {
                        setLookup(llmopsErrorMessage(err))
                      }
                    })()
                  }}
                >
                  Which prompt ran?
                </Button>
              </div>
              {lookup && (
                <p role="status" aria-live="polite" className="mb-3 text-sm break-words text-foreground">
                  {lookup}
                </p>
              )}
              {runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No runs recorded yet. {runWindow}
                </p>
              ) : (
                <>
                  <ul className="min-w-0 divide-y divide-border rounded-lg border border-border">
                    {runs.map((run) => (
                      <li
                        key={run.runId}
                        className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
                      >
                        <Figure
                          label={`run ${run.runId}`}
                          className="min-w-0 break-words text-muted-foreground"
                        >
                          {run.runId.slice(0, 12)}
                        </Figure>
                        <span className="min-w-0 text-foreground">
                          {run.version == null
                            ? 'ran on the shipped prompt'
                            : `ran on version ${run.version}`}
                        </span>
                        <Figure className="ml-auto text-xs leading-5 text-muted-foreground">
                          {RUN_TIME_FORMAT.format(new Date(run.ts))}
                        </Figure>
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-xs text-muted-foreground">{runWindow}</p>
                </>
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

/**
 * One version in the history, with the control that makes it live.
 *
 * **Making a version live is the irreversible act on this screen**, and it now asks
 * first. Every run in the scope changes on the next request, the prompt it displaces is
 * archived, and there is no undo the operator can reach here. The confirmation is the
 * same shape the rest of the control planes use (`memoryctl/FactManager`,
 * `memoryctl/RetentionPanel`): a sentence naming *what* changes and *what it replaces*,
 * a destructive button, and "Keep it". A version with no body is refused rather than
 * confirmed, for the reason given at `saveDraft`.
 */
function VersionLine({
  row,
  busy,
  editable,
  scopeLabel,
  replaces,
  confirming,
  onArm,
  onActivate,
  onLoad,
}: {
  row: PromptVersionRow
  busy: boolean
  editable: boolean
  /** Whose prompt this is, so the confirmation names the blast radius. */
  scopeLabel: string
  /** What is live now, in words — "the shipped prompt", or "version 3". */
  replaces: string
  confirming: boolean
  onArm: (on: boolean) => void
  onActivate: () => void
  onLoad: () => void
}): ReactElement {
  const confirmRef = useRef<HTMLButtonElement>(null)
  const empty = row.systemPrompt.trim().length === 0

  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  return (
    <li className="min-w-0 px-3 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
        <span className="min-w-0 text-sm font-medium text-foreground">Version {row.version}</span>
        <span className="text-xs text-muted-foreground">{row.status}</span>
        {row.isActive && (
          <span className="inline-flex items-center gap-1 rounded-md bg-success/12 px-2 py-0.5 text-xs text-success">
            <CheckCircle2 className="size-3" aria-hidden="true" /> live
          </span>
        )}
        {row.createdBy && (
          <span className="text-xs text-muted-foreground">by {row.createdBy}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onLoad}>
            Open
          </Button>
          {!row.isActive && editable && !confirming && (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => onArm(true)}>
              Make live
            </Button>
          )}
        </div>
      </div>

      {confirming && (
        <div className="mt-2 flex min-w-0 flex-col gap-2 rounded-md border border-danger/40 bg-danger/[0.06] p-3">
          <p role="alert" className="flex items-start gap-1.5 text-sm break-words text-foreground">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
            <span>
              {empty
                ? `Version ${row.version} has no body. Making it live would leave ${scopeLabel} with only the platform floor.`
                : `Make version ${row.version} live? Every run in ${scopeLabel} uses it from the next request, replacing ${replaces}.`}
            </span>
          </p>
          <div className="flex flex-wrap items-center gap-2">
            {!empty && (
              <Button
                ref={confirmRef}
                variant="destructive"
                size="sm"
                disabled={busy}
                onClick={onActivate}
              >
                {busy && <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden />}
                {busy ? 'Activating…' : `Make version ${row.version} live`}
              </Button>
            )}
            <Button
              ref={empty ? confirmRef : undefined}
              variant="ghost"
              size="sm"
              onClick={() => onArm(false)}
            >
              Keep it
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}
