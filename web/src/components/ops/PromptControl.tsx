'use client'

import { CheckCircle2, Loader2, Lock, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
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

  const load = useCallback(async () => {
    if (!hydrated) return
    setLoading(true)
    setError(null)
    try {
      const next = await getPromptScreen(token, PROMPT_KEY)
      setScreen(next)
      setDraft((current) => current || next.activePrompt || '')
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
  }, [token, hydrated])

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

  return (
    <Card>
      <CardHeader
        eyebrow={screen ? screen.scopeLabel : 'prompt control'}
        title="Live prompt"
        actions={
          <InfoTip label="About prompt control">
            The prompt your assistant is running right now, for this tenant only. Save a new
            version, activate it, and every run from that moment uses it — no deploy, no
            restart. The platform floor below it is composed underneath every version and
            cannot be edited from here.
          </InfoTip>
        }
      />
      <CardBody>
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 motion-safe:animate-spin" /> Loading the live prompt…
          </div>
        ) : error && screen == null ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : screen == null ? null : (
          <div className="space-y-6">
            {/* What is live, right now. */}
            <div className="flex flex-wrap items-center gap-3">
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
              <textarea
                id="prompt-draft"
                value={draft}
                readOnly={!editable}
                onChange={(e) => setDraft(e.target.value)}
                rows={8}
                className="w-full rounded-lg border border-border bg-card px-3 py-2 font-mono text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={busy || !editable || draft.trim().length === 0}
                  onClick={() => void saveDraft()}
                >
                  {busy && <Loader2 className="size-4 motion-safe:animate-spin" />}
                  Save as a new version
                </Button>
                {!editable && (
                  <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
                    <Lock className="size-4" aria-hidden="true" /> This prompt belongs to the
                    platform and is read-only here.
                  </span>
                )}
              </div>
            </div>

            {note && <p className="text-sm text-success">{note}</p>}
            {error && <p className="text-sm text-danger">{error}</p>}

            {/* History. */}
            <div>
              <h4 className="t-label mb-2 text-foreground">Versions</h4>
              {screen.versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No versions yet. Saving one puts it here as a draft.
                </p>
              ) : (
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {screen.versions.map((row) => (
                    <VersionLine
                      key={row.id}
                      row={row}
                      busy={busy}
                      editable={editable}
                      onActivate={() =>
                        void act(
                          () => activatePromptVersion(token, row.id),
                          `Version ${row.version} is live. Every run from now uses it.`,
                        )
                      }
                      onLoad={() => setDraft(row.systemPrompt)}
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
              <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-xs text-muted-foreground">
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
                <input
                  id="run-lookup"
                  value={lookupId}
                  placeholder="Paste a run id"
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
              {lookup && <p className="mb-3 text-sm text-foreground">{lookup}</p>}
              {runs.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No runs recorded yet. {runWindow}
                </p>
              ) : (
                <>
                  <ul className="divide-y divide-border rounded-lg border border-border">
                    {runs.map((run) => (
                      <li
                        key={run.runId}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
                      >
                        <code className="font-mono text-xs text-muted-foreground">
                          {run.runId.slice(0, 12)}
                        </code>
                        <span className="text-foreground">
                          {run.version == null
                            ? 'ran on the shipped prompt'
                            : `ran on version ${run.version}`}
                        </span>
                        <span className="ml-auto text-xs text-muted-foreground">
                          {new Date(run.ts).toLocaleString()}
                        </span>
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

/** One version in the history, with the control that makes it live. */
function VersionLine({
  row,
  busy,
  editable,
  onActivate,
  onLoad,
}: {
  row: PromptVersionRow
  busy: boolean
  editable: boolean
  onActivate: () => void
  onLoad: () => void
}): ReactElement {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
      <span className="text-sm font-medium text-foreground">Version {row.version}</span>
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
        {!row.isActive && editable && (
          <Button variant="outline" size="sm" disabled={busy} onClick={onActivate}>
            Make live
          </Button>
        )}
      </div>
    </li>
  )
}
