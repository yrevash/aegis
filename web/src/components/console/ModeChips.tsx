'use client'

import { ChevronDown, UserRound } from 'lucide-react'
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
 *   the classifier. {@link ModeMenu} is the missing half.
 * - **Persona** scopes the data and the tool roster and always did.
 * - **Model** is a *report*: `GET /models` answers what the gateway would actually do.
 *   A per-user preference is `agent.model` in settings, resolved platform → tenant →
 *   user with a badge naming the scope that decided; a second writer here, with no room
 *   for that badge, would reintroduce the ambiguity that screen exists to remove.
 *
 * ## Why the three read as one row now
 *
 * They were three different kinds of object side by side — two bordered menu chips
 * carrying `Label: value`, and between them a bare rounded-full `<select>` with its name
 * hidden in an `sr-only` label. Three shapes for three controls of equal weight reads as
 * three competing widgets, and the one whose label was invisible was the one nobody could
 * name. All three are the same chip now: icon, the control's name, its current value, a
 * chevron. Persona stays a **native `<select>`** inside that chip rather than becoming a
 * fourth custom popover — a native listbox is the better control on a phone and the
 * better one for assistive tech, and matching its geometry costs nothing.
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
    <div className={cn('flex min-w-0 flex-wrap items-center', compact ? 'gap-1.5' : 'gap-2')}>
      <ModeMenu mode={mode} onModeChange={onModeChange} />

      {personas.length > 1 && (
        <div
          className={cn(
            'inline-flex h-8 max-w-[14rem] min-w-0 items-center gap-1.5 rounded-md border border-input bg-surface/60 px-2',
            'text-[0.78rem] transition-colors focus-within:ring-2 focus-within:ring-ring/40 hover:bg-surface-2',
          )}
        >
          <UserRound aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
          <label htmlFor="composer-persona" className="shrink-0 text-muted-foreground">
            Persona:
          </label>
          <select
            id="composer-persona"
            value={persona?.id}
            onChange={(event) => onPersonaChange(event.target.value)}
            className="min-w-0 appearance-none truncate bg-transparent font-medium text-foreground outline-none"
          >
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <ChevronDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        </div>
      )}

      <ModelsMenu token={token} />
    </div>
  )
}
