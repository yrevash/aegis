'use client'

import { Gauge, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import { getPersona, personasForRole } from '@/config/personas'
import type { Role } from '@/lib/stream'

import { DEPTH_CHOICES, FANOUT_CHOICES, type RunMode } from './runMode'
import { ModelsMenu } from './ModelsMenu'

/**
 * One chip in the mode row. Fully round per DESIGN.md §1, filled in `--blue-600` when
 * selected (a fill step, not a text step) and a hairline otherwise.
 */
function Chip({
  selected,
  onClick,
  title,
  children,
  className,
}: {
  selected: boolean
  onClick: () => void
  title?: string
  children: React.ReactNode
  className?: string
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      title={title}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[0.78rem] font-medium',
        'outline-none transition-colors duration-[var(--dur-fast)]',
        'focus-visible:ring-2 focus-visible:ring-ring',
        selected
          ? 'bg-primary text-primary-foreground'
          : 'border border-border bg-surface/70 text-muted-foreground hover:border-blue-200 hover:text-blue-700',
        className,
      )}
    >
      {children}
    </button>
  )
}

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
 *   the classifier. These chips are the missing half.
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
      <span className="sr-only" id="mode-chips-label">
        How wide this turn runs
      </span>
      <div
        role="group"
        aria-labelledby="mode-chips-label"
        className={cn('flex flex-wrap items-center', compact ? 'gap-1' : 'gap-1.5')}
      >
        <Gauge aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        {DEPTH_CHOICES.map((choice) => (
          <Chip
            key={choice.id}
            selected={mode.depth === choice.id}
            title={choice.hint}
            onClick={() =>
              onModeChange({
                depth: choice.id,
                // A fanout only exists in Team; leaving one behind would be posted and
                // refused, since the server rejects it in any other mode.
                fanout: choice.id === 'team' ? mode.fanout : null,
              })
            }
          >
            {choice.label}
          </Chip>
        ))}
      </div>

      {mode.depth === 'team' && (
        <div
          role="group"
          aria-label="How many agents"
          className={cn('flex flex-wrap items-center', compact ? 'gap-1' : 'gap-1.5')}
        >
          <Users aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
          <Chip
            selected={mode.fanout === null}
            title="Let the supervisor size the team from the question."
            onClick={() => onModeChange({ depth: 'team', fanout: null })}
          >
            Auto
          </Chip>
          {FANOUT_CHOICES.map((n) => (
            <Chip
              key={n}
              selected={mode.fanout === n}
              title={`Ask for ${n} concurrent agents. The tenant cap can clamp this down.`}
              onClick={() => onModeChange({ depth: 'team', fanout: n })}
              className="tabular font-mono"
            >
              {n}
            </Chip>
          ))}
        </div>
      )}

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
