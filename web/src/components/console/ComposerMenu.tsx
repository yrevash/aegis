'use client'

import { ChevronDown } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * One control in the composer's button row: a labelled chip that opens a panel.
 *
 * The chip carries its own name and its current value (`Model: gpt-4o-mini`) because a
 * row of controls whose values are hidden until you open them is a row you have to open
 * to read. Escape and a click outside both close it — a panel that only closes by
 * re-clicking its own button is one people leave open by accident.
 *
 * It is a plain button rather than a `<select>` because the panel is a table with a
 * price per row, and it sits inside the composer's `<form>`, so it is explicitly
 * `type="button"`: a default-typed button in a form submits it, which here would send
 * the question.
 */
export function ComposerMenu({
  label,
  value,
  icon,
  children,
  disabled = false,
  align = 'left',
  width = 'default',
}: {
  /** The control's name, sentence case. */
  label: string
  /** Its current value, shown on the chip. */
  value: string
  icon?: ReactNode
  /**
   * The panel's contents. Given as a function when the panel closes on a choice —
   * a mode menu that stayed open after Team was picked would cover the composer's
   * own degree row, which is the thing the choice just revealed.
   */
  children: ReactNode | ((close: () => void) => ReactNode)
  disabled?: boolean
  /** Which edge the panel hangs from. */
  align?: 'left' | 'right'
  /** Widen the panel when its rows carry a sentence each rather than a word. */
  width?: 'default' | 'wide'
}): ReactElement {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent): void => {
      // Focus returns to the chip, so Escape does not drop the person at the top of
      // the document with the composer still under their cursor.
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    const onDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Node)) return
      if (boxRef.current?.contains(event.target) === true) return
      setOpen(false)
    }
    // Tabbing past the last control in the panel closes it, so a keyboard user is not
    // left with an open panel hanging over the composer they have moved on from.
    // `relatedTarget` is null for a click on a non-focusable part of the panel, which is
    // not a departure and must not close it.
    const onFocusOut = (event: FocusEvent): void => {
      const next = event.relatedTarget
      if (!(next instanceof Node)) return
      if (boxRef.current?.contains(next) === true) return
      setOpen(false)
    }
    const box = boxRef.current
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    box?.addEventListener('focusout', onFocusOut)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
      box?.removeEventListener('focusout', onFocusOut)
    }
  }, [open])

  return (
    <div ref={boxRef} className="relative min-w-0">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        // `dialog`, not `menu`: these panels are a priced table, a dropzone and a set of
        // named choices, none of which behaves like a menubar menu. Announcing one and
        // shipping the other is how a screen reader ends up promising arrow keys that
        // are not there.
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((was) => !was)}
        className={cn(
          'inline-flex h-8 max-w-[14rem] items-center gap-1.5 rounded-md border border-input bg-surface/60 px-2',
          'text-[0.78rem] text-foreground outline-none transition-colors',
          'hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring/40',
          'disabled:cursor-not-allowed disabled:opacity-60',
        )}
      >
        {icon}
        <span className="text-muted-foreground">{label}:</span>
        <span className="min-w-0 truncate font-medium">{value}</span>
        <ChevronDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div
          id={panelId}
          className={cn(
            'absolute bottom-full z-30 mb-1.5 max-w-[85vw] rounded-lg border border-border bg-card p-3 shadow-pop',
            width === 'wide' ? 'w-[24rem]' : 'w-[22rem]',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {typeof children === 'function'
            ? children(() => {
                setOpen(false)
                triggerRef.current?.focus()
              })
            : children}
        </div>
      )}
    </div>
  )
}
