'use client'

import { ALargeSmall } from 'lucide-react'
import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from 'react'

import { cn } from '@/lib/utils'

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

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent): void => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
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
        if (open && !event.currentTarget.contains(event.relatedTarget as Node | null)) {
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
