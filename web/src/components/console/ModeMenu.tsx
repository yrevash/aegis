'use client'

import { Check, Gauge, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { ComposerMenu } from './ComposerMenu'
import { DEPTH_CHOICES, FANOUT_CHOICES, describeMode, type RunMode } from './runMode'

/**
 * The width dropdown — Auto · Single · Team, named, with what each one does.
 *
 * The three widths were reachable before this only as three chips in a row, which is
 * the right control for a value people set constantly and the wrong one for the axis
 * that decides whether this product shows a single lane or the fan-out it is actually
 * built around. A chip row states three words with no room to say what any of them
 * costs, and it reads as decoration beside the send button rather than as *the* choice
 * for the turn.
 *
 * So: one labelled chip carrying the current value (`Mode: Team of 3`), opening a panel
 * of three named rows, each with the sentence that says what choosing it does.
 *
 * **Auto and Single close the panel; Team does not.** Team is the one choice that has a
 * second question attached — how many agents — and closing on it would hide the degree
 * row the choice just revealed behind a second click. The chosen degree is on the chip
 * itself, so the width is readable without opening anything, which is the reason the
 * chip carries its value at all.
 *
 * What this control emphatically does *not* do is report the outcome. `depth_mode` is a
 * request; the run answers with a `routing` event carrying `decided_by`, and the tenant
 * cap can clamp a five-agent ask to three. That answer is rendered from the run's own
 * receipt in {@link WidthReceipt}, never echoed back from this selection.
 */
export function ModeMenu({
  mode,
  onModeChange,
  disabled = false,
}: {
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  /** True while a run is in flight — the width belongs to the turn it was sent with. */
  disabled?: boolean
}): ReactElement {
  return (
    <ComposerMenu
      label="Mode"
      value={describeMode(mode)}
      disabled={disabled}
      width="wide"
      icon={<Gauge aria-hidden className="size-3.5 shrink-0 text-blue-700" />}
    >
      {(close) => (
        <div role="group" aria-label="How wide this turn runs" className="flex flex-col gap-1">
          <p className="eyebrow mb-1">How wide this turn runs</p>
          {DEPTH_CHOICES.map((choice) => {
            const selected = mode.depth === choice.id
            return (
              <button
                key={choice.id}
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  onModeChange({
                    depth: choice.id,
                    // A fanout only exists in Team; leaving one behind would be posted
                    // and refused, since the server rejects it in any other mode.
                    fanout: choice.id === 'team' ? mode.fanout : null,
                  })
                  // Team opens a second question rather than answering one.
                  if (choice.id !== 'team') close()
                }}
                className={cn(
                  'flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left outline-none',
                  'transition-colors duration-[var(--dur-fast)]',
                  'hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring',
                  selected && 'bg-blue-50',
                )}
              >
                <Check
                  aria-hidden
                  className={cn(
                    'mt-0.5 size-3.5 shrink-0',
                    selected ? 'text-blue-700' : 'text-transparent',
                  )}
                />
                <span className="min-w-0">
                  <span
                    className={cn(
                      'block text-[0.82rem] font-medium',
                      selected ? 'text-blue-700' : 'text-foreground',
                    )}
                  >
                    {choice.label}
                  </span>
                  <span className="block text-[0.72rem] leading-snug text-muted-foreground">
                    {choice.hint}
                  </span>
                </span>
              </button>
            )
          })}

          {mode.depth === 'team' && (
            <div className="mt-1 border-t border-border pt-2">
              <p className="eyebrow mb-1.5 flex items-center gap-1.5">
                <Users aria-hidden className="size-3.5" />
                How many agents
              </p>
              <div className="flex flex-wrap items-center gap-1.5">
                <DegreeButton
                  selected={mode.fanout === null}
                  label="Auto"
                  title="Let the supervisor size the team from the question."
                  onClick={() => onModeChange({ depth: 'team', fanout: null })}
                />
                {FANOUT_CHOICES.map((n) => (
                  <DegreeButton
                    key={n}
                    selected={mode.fanout === n}
                    label={String(n)}
                    mono
                    title={`Ask for ${n} concurrent agents. The tenant cap can clamp this down.`}
                    onClick={() => onModeChange({ depth: 'team', fanout: n })}
                  />
                ))}
              </div>
              <p className="mt-2 text-[0.72rem] leading-snug text-muted-foreground">
                A request, not a guarantee: the tenant’s parallel-agent cap can clamp it,
                and the run says who decided.
              </p>
            </div>
          )}
        </div>
      )}
    </ComposerMenu>
  )
}

/**
 * One degree of fan-out. Fully round per DESIGN.md §1.
 *
 * Selected is `bg-primary`, the same filled treatment every other committing control in
 * this product wears, rather than `--blue-600`: `--primary` is `#101828` here, and a
 * lone blue pill beside a near-black Send button would read as a different kind of
 * control rather than the same one in a chosen state.
 */
function DegreeButton({
  selected,
  label,
  title,
  onClick,
  mono = false,
}: {
  selected: boolean
  label: string
  title: string
  onClick: () => void
  mono?: boolean
}): ReactElement {
  return (
    <button
      type="button"
      aria-pressed={selected}
      title={title}
      onClick={onClick}
      className={cn(
        'inline-flex items-center rounded-full px-3 py-1 text-[0.78rem] font-medium outline-none',
        'transition-colors duration-[var(--dur-fast)] focus-visible:ring-2 focus-visible:ring-ring',
        mono && 'tabular font-mono',
        selected
          ? 'bg-primary text-primary-foreground'
          : 'border border-border bg-surface/70 text-muted-foreground hover:border-blue-200 hover:text-blue-700',
      )}
    >
      {label}
    </button>
  )
}
