'use client'

import { ALargeSmall } from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from 'react'

import { cn } from '@/lib/utils'

import { shouldCloseOnBlur, shouldCloseOnPointerDown } from './menuDismiss'
import { TextSizeChoice } from './TextSizeChoice'
import { useTextScale } from './useTextScale'

/**
 * Text size, from anywhere in the console.
 *
 * The control's home is Settings, and this is the same control in the top bar — because
 * the person it exists for is the person who cannot comfortably read the screen they are
 * on, and asking them to find a Settings section first is asking them to do the thing
 * they came here because they cannot do. Both draw the same {@link TextSizeChoice} over
 * the same shared step, so they can never disagree.
 *
 * A disclosure, not a modal: `Escape` closes it and returns the caret to the trigger, a
 * click outside closes it, and tabbing out of it closes it and carries on down the page.
 * Which of those a given event is, is {@link shouldCloseOnBlur} /
 * {@link shouldCloseOnPointerDown}'s decision — see that file for why a blur naming
 * nothing used to unmount this panel under the user's own finger.
 */
export function TextSizeMenu(): ReactElement {
  const [scale] = useTextScale()
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => {
    setOpen(false)
    triggerRef.current?.focus()
  }, [])

  // Pointer dismissal. `pointerdown` rather than `mousedown` so a touch is dismissed by
  // the touch itself rather than by the synthetic mouse event a phone fires ~300ms later.
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent): void => {
      const wrap = wrapRef.current
      if (wrap === null) return
      if (shouldCloseOnPointerDown(true, event.target as Node | null, (n) => wrap.contains(n))) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  return (
    <div
      ref={wrapRef}
      className="relative"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.preventDefault()
          close()
        }
      }}
      onBlur={(event) => {
        const wrap = event.currentTarget
        if (shouldCloseOnBlur(open, event.relatedTarget as Node | null, (n) => wrap.contains(n))) {
          setOpen(false)
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={`Text size, ${scale} percent`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
        className={cn(
          'inline-flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-lg border border-border bg-surface text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 hover:text-foreground',
          'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface',
        )}
      >
        <ALargeSmall className="size-5" aria-hidden />
      </button>

      {open ? (
        <div
          id={panelId}
          /* Viewport-anchored below `sm` for the same reason the alert panel is: a
             right-aligned popover wider than the space left of its trigger runs off the
             left edge of a phone, and is clipped rather than scrolled. */
          className="fixed inset-x-4 top-[4.5rem] z-40 rounded-lg border border-border bg-popover p-2 shadow-pop sm:absolute sm:inset-x-auto sm:right-0 sm:top-[calc(100%+0.5rem)] sm:w-[15rem]"
        >
          <TextSizeChoice dense />
        </div>
      ) : null}
    </div>
  )
}
