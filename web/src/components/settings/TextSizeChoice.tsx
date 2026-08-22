'use client'

import { useId, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { cn } from '@/lib/utils'

import { TEXT_SCALES } from './textScale'
import { useTextScale } from './useTextScale'

/**
 * The text-size control — four steps, as one segmented radio group.
 *
 * **Native radios, hidden but real.** The segments are `<input type="radio">` inside
 * their `<label>`s, so arrow-key navigation, the roving tab stop, the group semantics
 * and the announced selected state are the platform's rather than a re-implementation
 * of them with `role="radio"` and a `keydown` handler. All that is styled is the box
 * around each one, off `peer-checked` and `peer-focus-visible`.
 *
 * **The sample scales with the step.** Each segment draws an `A` at that step's own
 * size, so the control shows what it does instead of describing it — and the percentage
 * underneath is the exact value, in the mono face every other figure in the console
 * uses. `dense` drops the percentage for the top-bar popover, where the `A` ramp and
 * the accessible label carry it.
 */
export function TextSizeChoice({
  dense = false,
  className,
}: {
  dense?: boolean
  className?: string
}): ReactElement {
  const [scale, setScale] = useTextScale()
  const name = useId()

  return (
    <fieldset className={cn('min-w-0', className)}>
      <legend className="sr-only">Text size</legend>
      <div className="flex min-w-0 items-stretch gap-1 rounded-lg border border-border bg-surface-2/70 p-1">
        {TEXT_SCALES.map((step, index) => {
          const on = scale === step.percent
          return (
            <label
              key={step.percent}
              className="relative flex min-w-0 flex-1 cursor-pointer touch-manipulation"
            >
              <input
                type="radio"
                name={name}
                value={step.percent}
                checked={on}
                onChange={() => setScale(step.percent)}
                className="peer sr-only"
              />
              {/* The whole visual is `aria-hidden`, so the radio's accessible name is
                  the sentence below and nothing else. Without it the name computes as
                  "125% Largest text, 125 percent" — the sample glyph is decorative and
                  the percentage is said properly a line later. */}
              <span
                aria-hidden
                className={cn(
                  'flex min-h-11 w-full flex-col items-center justify-center gap-0.5 rounded-md px-2 leading-none transition-colors duration-[--dur-fast]',
                  'peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-1 peer-focus-visible:ring-offset-surface-2',
                  on
                    ? 'bg-surface font-semibold text-blue-700 shadow-card'
                    : 'text-muted-foreground hover:bg-surface/70 hover:text-foreground',
                )}
              >
                <span
                  style={{ fontSize: `${(0.72 + index * 0.14).toFixed(2)}rem` }}
                  className="font-semibold"
                >
                  A
                </span>
                {dense ? null : (
                  <Figure className="text-[0.6rem] leading-none">{step.percent}%</Figure>
                )}
              </span>
              <span className="sr-only">
                {step.label} text, {step.percent} percent
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
