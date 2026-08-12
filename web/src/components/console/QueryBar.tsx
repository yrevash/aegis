'use client'

import { CornerDownLeft, Play, RotateCcw } from 'lucide-react'
import { useState, type FormEvent, type ReactElement } from 'react'

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

/** Query input with persona selection and one-click sample prompts. */
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

  const submit = (e: FormEvent): void => {
    e.preventDefault()
    if (query.trim().length > 0) onRun(query.trim())
  }

  return (
    <div className="space-y-2.5">
      <form onSubmit={submit} className="flex flex-wrap items-center gap-2">
        {personas.length > 1 && (
          <select
            value={persona?.id}
            onChange={(e) => onPersonaChange(e.target.value)}
            className="h-9 rounded-md border border-input bg-surface/60 px-2.5 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
            aria-label="Persona"
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        <div className="relative min-w-0 flex-1">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask the agent to resolve or act on something…"
            className="pr-9"
            aria-label="Query"
          />
          <CornerDownLeft className="pointer-events-none absolute top-1/2 right-3 size-3.5 -translate-y-1/2 text-muted-foreground/60" />
        </div>
        <Button type="submit" disabled={running || query.trim().length === 0}>
          <Play className="size-4" /> Run
        </Button>
        <Button type="button" variant="outline" onClick={onReset} disabled={running}>
          <RotateCcw className="size-4" /> Reset
        </Button>
      </form>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="eyebrow mr-1">Try</span>
        {persona?.sampleQueries.map((q) => (
          <button
            key={q}
            type="button"
            disabled={running}
            onClick={() => setQuery(q)}
            className="max-w-full truncate rounded-md border border-border/70 bg-surface/40 px-2.5 py-1 text-left font-mono text-[0.7rem] text-muted-foreground transition-colors hover:border-agent/40 hover:text-agent-ink disabled:opacity-50"
            title={q}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}