'use client'

import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import { getPersona, personasForRole } from '@/config/personas'
import type { Role } from '@/lib/stream'

import { ModeMenu } from './ModeMenu'
import type { RunMode } from './runMode'
import { ModelsMenu } from './ModelsMenu'

interface ModeChipsProps {
  role: Role
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  personaId: string
  onPersonaChange: (id: string) => void
  /** Bearer for the model report. */
  token: string | null
  /** Compact row (in the docked composer) vs the roomy idle row. */
  compact?: boolean
}

/**
 * The four axes of a turn, exposed where the question is written.
 *
 * Three of them **set** the run and one **reports** it, and the difference is
 * deliberate rather than an omission:
 *
 * - **Width** (`depth_mode` / `requested_fanout`) writes straight onto the wire. It has
 *   been carried by `QueryRequest` since Phase 5 and posted by `startRun` since, always
 *   as `null` — so every turn ran in Auto and the fan-out was reachable only by luck of
 *   the classifier. {@link ModeMenu} is the missing half: a named dropdown rather than
 *   the three unlabelled chips it replaced, because this is the axis that decides
 *   whether the audience sees one lane or the fan-out, and a chip row has no room to
 *   say what any of the three does.
 * - **Persona** scopes the data and the tool roster and always did.
 * - **Model** is a *report*: `GET /models` answers what the gateway would actually do.
 *   A per-user preference is `agent.model` in settings, resolved platform → tenant →
 *   user with a badge naming the scope that decided; a second writer here, with no room
 *   for that badge, would reintroduce the ambiguity that screen exists to remove.
 */
export function ModeChips({
  role,
  mode,
  onModeChange,
  personaId,
  onPersonaChange,
  token,
  compact = false,
}: ModeChipsProps): ReactElement {
  const personas = personasForRole(role)
  const persona = getPersona(personaId) ?? personas[0]

  return (
    <div className={cn('flex flex-wrap items-center', compact ? 'gap-1.5' : 'gap-2')}>
      <ModeMenu mode={mode} onModeChange={onModeChange} />

      {personas.length > 1 && (
        <>
          <label htmlFor="composer-persona" className="sr-only">
            Persona
          </label>
          <select
            id="composer-persona"
            value={persona?.id}
            onChange={(event) => onPersonaChange(event.target.value)}
            className="h-7 rounded-full border border-border bg-surface/70 px-2.5 text-[0.78rem] text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </>
      )}

      <ModelsMenu token={token} />
    </div>
  )
}
