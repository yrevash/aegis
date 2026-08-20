'use client'

import { Menu, X } from 'lucide-react'
import { usePathname } from 'next/navigation'
import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from 'react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { cn } from '@/lib/utils'
import { portalLabelFor, type Portal } from '@/lib/portal'

import { PortalNav } from './PortalNav'
import { activeSectionFrom } from './navGroups'

/** Everything inside the panel that can hold focus, in document order. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'

/**
 * The navigation below `lg` — the same section list, in a drawer.
 *
 * The rail is `hidden lg:flex`, and for a long time that was the whole story:
 * under 1024px the console had **no navigation at all**. A signed-in operator on
 * a tablet could reach exactly the section the URL already named and nothing
 * else, on a product whose entire proposition is five role-scoped portals. That
 * is not a polish item, so this is not a shrunken rail — it is a drawer sized
 * for a thumb, over the same {@link PortalNav} the rail draws.
 *
 * The modal behaviour is hand-rolled rather than imported: the project has Radix
 * tooltip, scroll-area, separator and slot, and no dialog. Everything a dialog
 * owes a keyboard user is here — `Escape` closes it, `Tab` cycles inside it,
 * focus lands on the close button when it opens and returns to the trigger when
 * it shuts, the page behind it cannot scroll, and the backdrop is a click target
 * rather than decoration.
 */
export function MobileNav({ portal }: { portal: Portal }): ReactElement {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  // Mounted-then-shown, so the panel has a frame at `-translate-x-full` to
  // transition *from*. This is the whole animation: one 200ms slide that
  // confirms a state change, which is all DESIGN.md §6 allows it to be.
  const [shown, setShown] = useState(false)
  const panelId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => setOpen(false), [])

  // A navigation that survives its own use is a navigation stuck over the page
  // it just opened. Any route change closes it, including the ones the drawer
  // did not initiate (a browser back, a redirect from a portal guard).
  useEffect(() => {
    setOpen(false)
  }, [pathname])

  useEffect(() => {
    if (!open) {
      setShown(false)
      return
    }
    const frame = requestAnimationFrame(() => setShown(true))
    // Captured now: by cleanup time React may have swapped the node out, and the
    // caret has to go back to the trigger that was on screen when this opened.
    const trigger = triggerRef.current

    // Focus starts on Close, not on the first link: a screen-reader user who
    // opened a sixteen-item list needs the way out before the list.
    closeRef.current?.focus()

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        close()
        return
      }
      if (event.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const stops = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (stops.length === 0) return
      const first = stops[0]
      const last = stops[stops.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    // The page behind a modal must not scroll under it — on iOS especially, a
    // scrollable body beneath an open sheet is how a reader loses their place.
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      // Return the caret to the control that opened the drawer, so a keyboard
      // user resumes where they were rather than at the top of the document.
      trigger?.focus()
    }
  }, [open, close])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        className="inline-flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-lg border border-border bg-card text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface lg:hidden"
      >
        <Menu className="size-5" aria-hidden />
        <span className="sr-only">Open the {portalLabelFor(portal).toLowerCase()} navigation</span>
      </button>

      {open ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            tabIndex={-1}
            onClick={close}
            className={cn(
              'absolute inset-0 h-full w-full cursor-default bg-foreground/25 transition-opacity duration-[--dur-base] motion-reduce:transition-none',
              shown ? 'opacity-100' : 'opacity-0',
            )}
          >
            <span className="sr-only">Close the navigation</span>
          </button>
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-modal="true"
            aria-label={`${portalLabelFor(portal)} navigation`}
            className={cn(
              'absolute inset-y-0 left-0 flex w-[min(20rem,88vw)] flex-col border-r border-border bg-surface transition-transform duration-[--dur-base] ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none',
              shown ? 'translate-x-0' : '-translate-x-full',
            )}
          >
            <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
              <AegisLockup size="sm" />
              <button
                ref={closeRef}
                type="button"
                onClick={close}
                className="inline-flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              >
                <X className="size-5" aria-hidden />
                <span className="sr-only">Close the navigation</span>
              </button>
            </div>

            {/* `overscroll-contain` so a flick at the end of the section list
                scrolls the drawer and stops, rather than chaining through to the
                page underneath it. */}
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-4">
              <PortalNav
                portal={portal}
                active={activeSectionFrom(pathname)}
                onNavigate={close}
                density="drawer"
              />
            </div>

            <p className="eyebrow border-t border-border px-4 py-3">{portalLabelFor(portal)}</p>
          </div>
        </div>
      ) : null}
    </>
  )
}
