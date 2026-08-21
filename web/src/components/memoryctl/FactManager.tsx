'use client'

import { Check, Loader2, Pencil, Plus, ShieldCheck, Trash2, X } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Input } from '@/components/primitives/input'
import {
  correctMemoryFact,
  deleteMemoryFact,
  getMemoryFacts,
  writeMemoryFact,
} from '@/lib/api/client'
import type { MemoryFactRow } from '@/lib/api/memory'

import { EmptyRow, ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { useAsync } from '@/components/memory/useAsync'

import { screeningNote } from './memoryControl'

/** The longest fact the backend will accept; mirrored so the counter is honest. */
const MAX_CHARS = 2000

/**
 * Write, correct and delete the facts held about one subject.
 *
 * The three verbs the memory screen never had, and each one is a different promise:
 *
 * **Write** goes through `POST /memory/facts`, which screens the text with the full
 * input rail *before* anything is stored. That is not a formality. A stored fact is
 * replayed into every future prompt for this subject as trusted context, so an
 * unscreened write is a prompt injection with a delay fuse — it costs the attacker
 * nothing today and arrives inside the model's context tomorrow. When the rail refuses,
 * the backend's own sentence is what appears below the box, because "the guardrails
 * refused this" is a decision the writer is entitled to read rather than a generic 422.
 *
 * **Correct** supersedes rather than overwrites. The replaced row stays in the belief
 * timeline (visible in the record below), so "what did it think last week, and who
 * changed it" keeps an answer. An edit box that quietly rewrote history would make the
 * write log a fiction.
 *
 * **Delete** is a real delete. The row is removed from the database, not flagged, and
 * the button says so before it is pressed — a confirmation that does not name what it
 * destroys is not consent.
 */
export function FactManager({
  token,
  subject,
  refreshKey,
  onChanged,
}: {
  token: string | null
  subject: string
  refreshKey: number
  onChanged: () => void
}): ReactElement {
  const facts = useAsync(
    () => getMemoryFacts(token, subject, false),
    [token, subject, refreshKey],
  )

  const [draft, setDraft] = useState('')
  const [predicate, setPredicate] = useState('')
  const [object, setObject] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<number | null>(null)
  const [editText, setEditText] = useState('')
  const [confirmId, setConfirmId] = useState<number | null>(null)

  const rows = facts.state.status === 'ready' ? facts.state.data.rows : []

  async function run(action: () => Promise<string | null>): Promise<void> {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      setNote(await action())
      onChanged()
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : 'That did not go through. Try it again.')
    } finally {
      setBusy(false)
    }
  }

  const submit = (): Promise<void> =>
    run(async () => {
      const result = await writeMemoryFact(token, {
        text: draft.trim(),
        subject,
        predicate: predicate.trim(),
        object: object.trim(),
      })
      setDraft('')
      setPredicate('')
      setObject('')
      return (
        screeningNote(result.verdict, result.redactions) ??
        `Saved. The agent will recall this${result.embedded ? '' : ' by recency until it is embedded'}.`
      )
    })

  const save = (factId: number): Promise<void> =>
    run(async () => {
      const result = await correctMemoryFact(token, factId, { text: editText.trim() })
      setEditingId(null)
      return (
        screeningNote(result.verdict, result.redactions) ??
        `Corrected. Fact #${result.supersedes_id} was superseded and stays in the history below.`
      )
    })

  const remove = (factId: number): Promise<void> =>
    run(async () => {
      await deleteMemoryFact(token, factId)
      setConfirmId(null)
      return `Deleted fact #${factId}. The row is gone from the database.`
    })

  return (
    <div className="flex flex-col gap-4">
      {/* ── Write ───────────────────────────────────────────────────────── */}
      <form
        className="flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (draft.trim() !== '' && !busy) void submit()
        }}
      >
        <span className="flex items-center gap-1.5">
          <label className="eyebrow" htmlFor="memory-fact-text">
            Teach it something
          </label>
          {/* The screening mechanism is a real constraint a writer cannot infer,
              so it stays — one layer down, in one sentence. */}
          <InfoTip label="What happens to a fact on the way in">
            Screened by the same guardrails a live question goes through, because a stored
            memory is replayed into later prompts as trusted context.
          </InfoTip>
        </span>
        <textarea
          id="memory-fact-text"
          name="fact_text"
          value={draft}
          maxLength={MAX_CHARS}
          rows={3}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. Prefers email over phone for anything non-urgent…"
          className="w-full resize-y rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        <div className="flex flex-wrap items-center gap-2">
          {/* Relation and value are triple keys, not prose: no autocomplete
              history, no spellcheck squiggle, no machine translation. */}
          <Input
            aria-label="Relation, optional"
            name="predicate"
            value={predicate}
            maxLength={64}
            autoComplete="off"
            spellCheck={false}
            translate="no"
            onChange={(e) => setPredicate(e.target.value)}
            placeholder="relation…"
            className="h-8 w-44"
          />
          <Input
            aria-label="Value, optional"
            name="object"
            value={object}
            maxLength={128}
            autoComplete="off"
            spellCheck={false}
            translate="no"
            onChange={(e) => setObject(e.target.value)}
            placeholder="value…"
            className="h-8 w-44"
          />
          <Figure className="ml-auto text-[0.62rem] leading-4 text-muted-foreground">
            {draft.length}/{MAX_CHARS}
          </Figure>
          {/* Enabled until the request starts, then a spinner in the icon's slot —
              never a button that goes dead with no sign anything happened. */}
          <Button type="submit" size="sm" disabled={busy || draft.trim() === ''}>
            {busy ? (
              <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <Plus className="size-3.5" aria-hidden />
            )}
            {busy ? 'Saving…' : 'Save fact'}
          </Button>
        </div>
        {/* A status hue with its icon and its word, not a paragraph. */}
        <p className="flex items-center gap-1.5 text-[0.68rem] text-muted-foreground">
          <ShieldCheck className="size-3.5 shrink-0 text-ok-ink" aria-hidden />
          Screened before it is stored
        </p>
      </form>

      {failure !== null && (
        <p role="alert" className="text-sm break-words text-destructive">
          {failure}
        </p>
      )}
      {note !== null && (
        <p role="status" aria-live="polite" className="text-sm break-words text-ok-ink">
          {note}
        </p>
      )}

      {/* ── Correct / delete ────────────────────────────────────────────── */}
      {facts.state.status === 'loading' && <LoadingRow label="Loading facts…" />}
      {facts.state.status === 'error' && <ErrorRow message={facts.state.message} />}
      {facts.state.status === 'ready' && rows.length === 0 && (
        <EmptyRow>Nothing believed yet — write the first fact above.</EmptyRow>
      )}
      {rows.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {rows.map((fact: MemoryFactRow) => (
            <li key={fact.id} className="rounded-lg border border-border bg-card">
              {editingId === fact.id ? (
                <div className="flex flex-col gap-2 p-3">
                  <textarea
                    aria-label={`Correct fact ${fact.id}`}
                    name={`fact_text_${fact.id}`}
                    value={editText}
                    maxLength={MAX_CHARS}
                    rows={2}
                    onChange={(e) => setEditText(e.target.value)}
                    className="w-full resize-y rounded-lg border border-input bg-surface px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      disabled={busy || editText.trim() === ''}
                      onClick={() => void save(fact.id)}
                    >
                      {busy ? (
                        <Loader2
                          className="size-3.5 animate-spin motion-reduce:animate-none"
                          aria-hidden
                        />
                      ) : (
                        <Check className="size-3.5" aria-hidden />
                      )}
                      {busy ? 'Saving…' : 'Save correction'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                      Cancel
                    </Button>
                    <span className="text-[0.66rem] text-muted-foreground">
                      The old wording is kept as history, not overwritten.
                    </span>
                  </div>
                </div>
              ) : confirmId === fact.id ? (
                <div className="flex flex-col gap-2 p-3">
                  <p role="alert" className="text-sm break-words text-foreground">
                    Delete “{fact.text}”? The row is removed from the database, not
                    hidden, and the agent stops recalling it immediately.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => void remove(fact.id)}
                    >
                      {busy ? (
                        <Loader2
                          className="size-3.5 animate-spin motion-reduce:animate-none"
                          aria-hidden
                        />
                      ) : (
                        <Trash2 className="size-3.5" aria-hidden />
                      )}
                      {busy ? 'Deleting…' : 'Delete permanently'}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirmId(null)}>
                      Keep it
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-3 py-2">
                  <span className="min-w-0 flex-1 text-sm break-words text-foreground">
                    {fact.text}
                  </span>
                  <Badge tone="neutral" className="tabular shrink-0 text-[0.56rem]">
                    #{fact.id}
                  </Badge>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Correct fact ${fact.id}`}
                    onClick={() => {
                      setEditingId(fact.id)
                      setEditText(fact.text)
                      setConfirmId(null)
                    }}
                  >
                    <Pencil className="size-3.5" aria-hidden />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Delete fact ${fact.id}`}
                    onClick={() => {
                      setConfirmId(fact.id)
                      setEditingId(null)
                    }}
                  >
                    <X className="size-3.5" aria-hidden />
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
