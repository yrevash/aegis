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
}: {
  /** The control's name, sentence case. */
  label: string
  /** Its current value, shown on the chip. */
  value: string
  icon?: ReactNode
  children: ReactNode
  disabled?: boolean
  /** Which edge the panel hangs from. */
  align?: 'left' | 'right'
}): ReactElement {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOpen(false)
    }
    const onDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Node)) return
      if (boxRef.current?.contains(event.target) === true) return
      setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [open])

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((was) => !was)}
        className={cn(
          'inline-flex h-8 max-w-[14rem] items-center gap-1.5 rounded-md border border-input bg-surface/60 px-2',
          'text-[0.78rem] text-foreground outline-none transition-colors',
          'hover:bg-surface-2 focus-visible:ring-[3px] focus-visible:ring-ring/40',
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
            'absolute bottom-full z-30 mb-1.5 w-[22rem] max-w-[85vw] rounded-xl border border-border bg-card p-3 shadow-card',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {children}
        </div>
      )}
    </div>
  )
}
