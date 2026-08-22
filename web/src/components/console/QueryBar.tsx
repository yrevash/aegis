'use client'

import { CornerDownLeft, Loader2, Play, RotateCcw } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type ReactElement } from 'react'

import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import { getPersona, personasForRole } from '@/config/personas'
import type { Role } from '@/lib/stream'

interface QueryBarProps {
  role: Role
  personaId: string
  onPersonaChange: (id: string) => void
  onRun: (query: string) => void
  onReset: () => void
  running: boolean
}

/**
 * Query input with persona selection and one-click sample prompts.
 *
 * Shared by the graph, harness and retrieval screens, so the accessibility rules it
 * carries are three screens' worth: a real `<label>` on both inputs rather than an
 * `aria-label` a sighted keyboard user never sees, every decorative glyph `aria-hidden`,
 * and a visible focus ring on the sample chips, which had none at all.
 *
 * **Run stays enabled until the request starts.** It used to grey out on an empty box,
 * which is a control that refuses without saying why; an empty submit hands the caret
 * back to the field instead. Once a run is in flight the button is genuinely disabled
 * and says so with a spinner.
 */
export function QueryBar({
  role,
  personaId,
  onPersonaChange,
  onRun,
  onReset,
  running,
}: QueryBarProps): ReactElement {
  const personas = personasForRole(role)
  const persona = getPersona(personaId) ?? personas[0]
  const [query, setQuery] = useState(persona?.sampleQueries[0] ?? '')
  const fieldRef = useRef<HTMLInputElement>(null)
  const personaId_ = useId()
  const queryId = useId()

  const submit = (e: FormEvent): void => {
    e.preventDefault()
    if (running) return
    if (query.trim().length === 0) {
      fieldRef.current?.focus()
      return
    }
    onRun(query.trim())
  }

  return (
    <div className="min-w-0 space-y-2.5">
      <form onSubmit={submit} aria-busy={running} className="flex min-w-0 flex-wrap items-center gap-2">
        {personas.length > 1 && (
          <>
            <label htmlFor={personaId_} className="sr-only">
              Persona
            </label>
            <select
              id={personaId_}
              value={persona?.id}
              onChange={(e) => onPersonaChange(e.target.value)}
              className="h-9 min-w-0 rounded-md border border-input bg-surface/60 px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </>
        )}
        <div className="relative min-w-0 flex-1">
          <label htmlFor={queryId} className="sr-only">
            Query
          </label>
          <Input
            id={queryId}
            ref={fieldRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask the agent to resolve or act on something…"
            className="pr-9"
          />
          <CornerDownLeft
            aria-hidden
            className="pointer-events-none absolute top-1/2 right-3 size-3.5 -translate-y-1/2 text-muted-foreground/60"
          />
        </div>
        <Button type="submit" disabled={running}>
          {running ? (
            <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
          ) : (
            <Play aria-hidden className="size-4" />
          )}
          {running ? 'Running' : 'Run'}
        </Button>
        <Button type="button" variant="outline" onClick={onReset} disabled={running}>
          <RotateCcw aria-hidden className="size-4" /> Reset
        </Button>
      </form>

      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <span className="eyebrow mr-1">Try</span>
        {persona?.sampleQueries.map((q) => (
          <button
            key={q}
            type="button"
            disabled={running}
            onClick={() => setQuery(q)}
            className="max-w-full min-w-0 truncate rounded-md border border-border/70 bg-surface/40 px-2.5 py-1 text-left font-mono text-[0.7rem] text-muted-foreground outline-none transition-colors hover:border-blue-200/40 hover:text-blue-700 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            title={q}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}
