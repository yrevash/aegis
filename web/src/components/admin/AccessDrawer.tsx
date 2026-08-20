'use client'

import { X } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react'

import { cn } from '@/lib/utils'

/**
 * The write drawer for Roles & Access.
 *
 * The screen used to stack three always-open forms — create a tenant, create a user,
 * set a cap — above the state they change, so the first two screenfuls of the
 * delegation console were empty inputs and the answer to *"who can do what here?"*
 * started below the fold. The forms are the same forms; they are simply not the first
 * thing the screen says any more. State first, editing second.
 *
 * It is a dialog rather than an expanding panel because a write on this screen is
 * modal in fact: an operator creating a tenant is not also reading the roster, and a
 * form that pushes the table it is about to change off-screen is the worst of both.
 *
 * Built here rather than imported because the design system has no dialog primitive
 * yet and this lane may not add one. Everything a modal owes its user is honoured:
 * `Escape` and the backdrop close it, focus enters on open and returns to whatever
 * opened it on close, `Tab` cycles inside, the page behind cannot scroll, and the
 * slide is `--dur-base` under normal motion and instant under `prefers-reduced-motion`.
 */
export function AccessDrawer({
  open,
  onClose,
  title,
  subtitle,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  /** One line under the title. The mechanism goes in an `InfoTip`, not here. */
  subtitle?: ReactNode
  children: ReactNode
}): ReactElement | null {
  const panelRef = useRef<HTMLDivElement | null>(null)
  // Flipped on the frame after mount so the panel has a closed position to
  // transition *from*. Rendering it open would make the slide a jump.
  const [shown, setShown] = useState(false)
  // Whatever had focus when the drawer opened, so it can be given back. Losing it
  // drops a keyboard user at the top of the document after every write.
  const returnTo = useRef<HTMLElement | null>(null)

  const focusables = useCallback((): HTMLElement[] => {
    const root = panelRef.current
    if (!root) return []
    return [
      ...root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((el) => el.offsetParent !== null || el === document.activeElement)
  }, [])

  useEffect(() => {
    if (!open) {
      setShown(false)
      return
    }
    const raf = requestAnimationFrame(() => setShown(true))
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null

    // The page behind a modal must not scroll; restoring the previous value rather
    // than clearing it keeps a second overlay from unlocking the first one's lock.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const first = focusables()[0] ?? panelRef.current
    first?.focus()

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) return
      const firstItem = items[0]
      const lastItem = items[items.length - 1]
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault()
        lastItem.focus()
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault()
        firstItem.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      returnTo.current?.focus()
    }
  }, [open, onClose, focusables])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* The backdrop is a button so a pointer user can dismiss, and is hidden from
          assistive tech because Escape is the announced way out. */}
      <button
        type="button"
        tabIndex={-1}
        aria-hidden
        onClick={onClose}
        className={cn(
          'absolute inset-0 cursor-default bg-foreground/25 transition-opacity duration-[--dur-base] motion-reduce:transition-none',
          shown ? 'opacity-100' : 'opacity-0',
        )}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="access-drawer-title"
        tabIndex={-1}
        className={cn(
          'relative flex h-full w-full max-w-[36rem] flex-col border-l border-border bg-background shadow-pop outline-none',
          'transition-transform duration-[--dur-base] ease-out motion-reduce:transition-none',
          shown ? 'translate-x-0' : 'translate-x-full',
        )}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border bg-surface px-5 py-4">
          <div className="min-w-0">
            <h2
              id="access-drawer-title"
              className="text-base font-semibold tracking-[-0.01em] text-foreground"
            >
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors duration-[--dur-fast] outline-none motion-reduce:transition-none hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" aria-hidden />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">
          {children}
        </div>
      </div>
    </div>
  )
}
