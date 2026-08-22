'use client'

import { Check, Gauge, Users } from 'lucide-react'
import { useId, useRef, type KeyboardEvent, type ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { ComposerMenu } from './ComposerMenu'
import { DEPTH_CHOICES, FANOUT_CHOICES, describeMode, type RunMode } from './runMode'

/**
 * Arrow-key navigation for a set of buttons that are one control between them.
 *
 * Both sets in this panel — three widths, five degrees — are single-select, so each is a
 * radiogroup: **one** tab stop, and the arrows move within it. They shipped as eight
 * independent `aria-pressed` buttons, which is eight tab stops between the composer's
 * Mode chip and its Send button, and no way to tell from the role that picking one
 * unpicks the others.
 *
 * Left/Up and Right/Down both work, because the two groups run horizontally and
 * vertically and a person cannot see which convention a given group was built to.
 * Selection follows focus, which is the pattern for a radiogroup whose options are
 * cheap to change — and neither of these costs anything until Send is pressed.
 *
 * @returns The index to move to, or `null` when the key was not ours to handle.
 */
function rovingIndex(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % count
  if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + count) % count
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  return null
}

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
 * row the choice just revealed behind a second click. Choosing with the **arrow keys**
 * never closes: selection follows focus there, and a panel that vanished under the third
 * arrow press would be unusable from the keyboard.
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
  // Generated rather than literal: two composers on one page (idle and docked never
  // coexist today, but nothing stops it) would otherwise share one `aria-labelledby`.
  const depthLabelId = useId()
  const degreeLabelId = useId()

  return (
    <ComposerMenu
      label="Mode"
      value={describeMode(mode)}
      disabled={disabled}
      width="wide"
      icon={<Gauge aria-hidden className="size-3.5 shrink-0 text-blue-700" />}
    >
      {(close) => (
        <div className="flex flex-col gap-1">
          <p className="eyebrow mb-1" id={depthLabelId}>
            How wide this turn runs
          </p>
          <DepthChoices
            mode={mode}
            onModeChange={onModeChange}
            close={close}
            labelId={depthLabelId}
          />

          {mode.depth === 'team' && (
            <div className="mt-1 border-t border-border pt-2">
              <p className="eyebrow mb-1.5 flex items-center gap-1.5" id={degreeLabelId}>
                <Users aria-hidden className="size-3.5" />
                How many agents
              </p>
              <DegreeChoices mode={mode} onModeChange={onModeChange} labelId={degreeLabelId} />
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

/** The three widths, as one radiogroup with one tab stop. */
function DepthChoices({
  mode,
  onModeChange,
  close,
  labelId,
}: {
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  close: () => void
  labelId: string
}): ReactElement {
  const items = useRef<(HTMLButtonElement | null)[]>([])
  const selectedIndex = Math.max(
    0,
    DEPTH_CHOICES.findIndex((choice) => choice.id === mode.depth),
  )

  const pick = (index: number): void => {
    const choice = DEPTH_CHOICES[index]
    if (choice === undefined) return
    onModeChange({
      // A fanout only exists in Team; leaving one behind would be posted and refused,
      // since the server rejects it in any other mode.
      depth: choice.id,
      fanout: choice.id === 'team' ? mode.fanout : null,
    })
  }

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const next = rovingIndex(event.key, index, DEPTH_CHOICES.length)
    if (next === null) return
    event.preventDefault()
    pick(next)
    items.current[next]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-labelledby={labelId}
      className="flex flex-col gap-1"
    >
      {DEPTH_CHOICES.map((choice, index) => {
        const selected = mode.depth === choice.id
        return (
          <button
            key={choice.id}
            ref={(el) => {
              items.current[index] = el
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={index === selectedIndex ? 0 : -1}
            onKeyDown={(event) => onKeyDown(event, index)}
            onClick={() => {
              pick(index)
              // Team opens a second question rather than answering one.
              if (choice.id !== 'team') close()
            }}
            className={cn(
              'flex w-full min-w-0 items-start gap-2 rounded-md px-2 py-1.5 text-left outline-none',
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
    </div>
  )
}

/** Auto, then the explicit team widths — one radiogroup, one tab stop. */
function DegreeChoices({
  mode,
  onModeChange,
  labelId,
}: {
  mode: RunMode
  onModeChange: (mode: RunMode) => void
  labelId: string
}): ReactElement {
  const items = useRef<(HTMLButtonElement | null)[]>([])
  /** `null` (Auto) is index 0; the explicit degrees follow in `FANOUT_CHOICES` order. */
  const degrees: readonly (number | null)[] = [null, ...FANOUT_CHOICES]
  const selectedIndex = Math.max(
    0,
    degrees.findIndex((n) => n === mode.fanout),
  )

  const pick = (index: number): void => {
    onModeChange({ depth: 'team', fanout: degrees[index] ?? null })
  }

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number): void => {
    const next = rovingIndex(event.key, index, degrees.length)
    if (next === null) return
    event.preventDefault()
    pick(next)
    items.current[next]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-labelledby={labelId}
      className="flex min-w-0 flex-wrap items-center gap-1.5"
    >
      {degrees.map((n, index) => (
        <DegreeButton
          key={n ?? 'auto'}
          ref={(el) => {
            items.current[index] = el
          }}
          selected={mode.fanout === n}
          roving={index === selectedIndex}
          label={n === null ? 'Auto' : String(n)}
          mono={n !== null}
          title={
            n === null
              ? 'Let the supervisor size the team from the question.'
              : `Ask for ${n} concurrent agents. The tenant cap can clamp this down.`
          }
          onKeyDown={(event) => onKeyDown(event, index)}
          onClick={() => pick(index)}
        />
      ))}
    </div>
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
  ref,
  selected,
  roving,
  label,
  title,
  onClick,
  onKeyDown,
  mono = false,
}: {
  ref: (el: HTMLButtonElement | null) => void
  selected: boolean
  /** Whether this is the group's single tab stop. */
  roving: boolean
  label: string
  title: string
  onClick: () => void
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void
  mono?: boolean
}): ReactElement {
  return (
    <button
      ref={ref}
      type="button"
      role="radio"
      aria-checked={selected}
      tabIndex={roving ? 0 : -1}
      title={title}
      onClick={onClick}
      onKeyDown={onKeyDown}
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
