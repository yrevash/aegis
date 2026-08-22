'use client'

import {
  Bell,
  BellRing,
  CheckCheck,
  Info,
  Inbox,
  OctagonAlert,
  TriangleAlert,
  Volume2,
  VolumeX,
  X,
  type LucideIcon,
} from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'

import {
  badgeCount,
  bellLabel,
  relativeAge,
  severityLabel,
  severityTone,
  streamNote,
} from './notificationState'
import { useNotifications } from './useNotifications'

/**
 * The alert bell — the console's one piece of chrome that speaks without being asked.
 *
 * There was a bell here once, fed by nothing, answering "you're all caught up" whatever
 * the platform was actually doing; it was deleted for that. This one is a projection of
 * `GET /notifications` and a live socket, and it says which of those two it is running
 * on at any moment. A hundred documents queued as a job finish while their operator is
 * on another screen, and the whole point is that they find out there rather than by
 * going back to look.
 *
 * **A status surface leads with a mark and keeps its detail one layer down** (DESIGN.md
 * §4). The mark is the bell itself: the glyph changes shape when something is unread,
 * the frame takes the blue wash, and the count rides the corner. The rows, the bodies
 * and the timestamps are the disclosure, and they are one click away.
 *
 * **The count is never carried by colour or by the badge alone.** The badge is a
 * numeral, the glyph is a different glyph, the button's own `aria-label` says the
 * number in words, and an arriving alert is announced through a polite live region — so
 * the state reaches a person who cannot see the corner of a button, and survives a
 * greyscale projector.
 *
 * **Not a modal.** The panel is a disclosure: `Escape` closes it and hands focus back,
 * a click outside closes it, and tabbing past the last row closes it and carries on
 * through the page — rather than a focus trap on a dropdown, which leaves a keyboard
 * user fenced inside a list they only wanted to glance at.
 */

/** The glyph per severity — shape, so the row is not told by hue alone. */
const SEVERITY_ICON: Readonly<Record<string, LucideIcon>> = {
  critical: OctagonAlert,
  warning: TriangleAlert,
  info: Info,
}

/** The one focus treatment on this surface. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface'

export function NotificationBell(): ReactElement {
  const feed = useNotifications()
  const [open, setOpen] = useState(false)
  // One clock for every row, ticked while the panel is open. Reading `Date.now()` per
  // row per render makes "4m" and "5m" appear in one list; this makes the ages agree.
  const [now, setNow] = useState(() => Date.now())
  const panelId = useId()
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => {
    setOpen(false)
    triggerRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    setNow(Date.now())
    closeRef.current?.focus()
    const tick = setInterval(() => setNow(Date.now()), 30_000)
    const onPointerDown = (event: MouseEvent): void => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => {
      clearInterval(tick)
      document.removeEventListener('mousedown', onPointerDown)
    }
  }, [open])

  const unread = feed.unread
  const latest = feed.arrived.length === 0 ? null : feed.arrived[feed.arrived.length - 1]
  const announced = latest === null ? null : (feed.rows.find((row) => row.id === latest) ?? null)

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
        // Focus left the bell entirely — tabbed past the last row, or clicked away.
        // Closing here is what keeps this a disclosure rather than a trap.
        if (open && !event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setOpen(false)
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={bellLabel(unread)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => setOpen((was) => !was)}
        className={cn(
          'relative inline-flex size-11 shrink-0 touch-manipulation items-center justify-center rounded-lg border transition-colors duration-[--dur-fast]',
          FOCUS,
          unread > 0
            ? 'border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100'
            : 'border-border bg-surface text-muted-foreground hover:bg-surface-2 hover:text-foreground',
        )}
      >
        {unread > 0 ? <BellRing className="size-5" aria-hidden /> : <Bell className="size-5" aria-hidden />}
        {unread > 0 ? (
          <span
            aria-hidden
            className="absolute -top-1 -right-1 inline-flex min-w-[1.15rem] items-center justify-center rounded-full bg-primary px-1 font-mono text-[0.65rem] leading-[1.15rem] font-medium text-primary-foreground"
          >
            {badgeCount(unread)}
          </span>
        ) : null}
      </button>

      {/* Always mounted, so an alert arriving while the panel is shut is still spoken.
          A live region added at the same moment as its content announces nothing. */}
      <p aria-live="polite" className="sr-only">
        {announced === null
          ? ''
          : `${severityLabel(announced.severity)} alert: ${announced.title}. ${announced.body}`}
      </p>

      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-labelledby={`${panelId}-title`}
          /*
           * Anchored to the viewport below `sm`, to the bell above it.
           *
           * `absolute right-0` aligns the panel's right edge with the trigger's, which
           * is correct while there is room to its left — and on a 390px phone there is
           * not: the bell sits ~120px from the right edge, so a 358px panel starts at
           * -88px and the first 88px of every alert is off-screen to the left. It
           * measured as no document overflow, because it was simply clipped. The
           * breakpoint is where the arithmetic stops failing (trigger inset + panel
           * width ≈ 506px), not a guess.
           */
          className="fixed inset-x-4 top-[4.5rem] z-40 overflow-hidden rounded-lg border border-border bg-popover shadow-pop sm:absolute sm:inset-x-auto sm:right-0 sm:top-[calc(100%+0.5rem)] sm:w-[min(24rem,calc(100vw-2rem))]"
        >
          <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
            <h2 id={`${panelId}-title`} className="text-sm font-semibold text-foreground">
              Alerts
            </h2>
            <span
              className={cn(
                'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[0.65rem] font-medium',
                feed.status === 'live'
                  ? 'bg-ok/15 text-ok-ink'
                  : feed.status === 'retrying' || feed.status === 'connecting'
                    ? 'bg-risk/15 text-risk-ink'
                    : 'bg-surface-2 text-muted-foreground',
              )}
            >
              <span
                aria-hidden
                className={cn(
                  'size-1.5 rounded-full',
                  feed.status === 'live'
                    ? 'bg-ok-ink'
                    : feed.status === 'closed'
                      ? 'bg-muted-foreground'
                      : 'bg-risk-ink',
                )}
              />
              {streamNote(feed.status)}
            </span>
            {unread > 0 ? (
              <button
                type="button"
                onClick={feed.markAllRead}
                className={cn(
                  'ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium text-blue-700 transition-colors duration-[--dur-fast] hover:bg-blue-50',
                  FOCUS,
                )}
              >
                <CheckCheck className="size-3.5" aria-hidden />
                Mark all read
              </button>
            ) : null}
            <button
              ref={closeRef}
              type="button"
              onClick={close}
              aria-label="Close alerts"
              className={cn(
                'ml-auto inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 hover:text-foreground',
                FOCUS,
              )}
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>

          <div className="max-h-[min(26rem,60vh)] overflow-y-auto overscroll-contain">
            {feed.error !== null ? (
              <p className="px-3 py-6 text-sm text-muted-foreground">{feed.error}</p>
            ) : feed.loading && feed.rows.length === 0 ? (
              <p className="px-3 py-6 text-sm text-muted-foreground">Reading the feed…</p>
            ) : feed.rows.length === 0 ? (
              <p className="flex items-center gap-2 px-3 py-6 text-sm text-muted-foreground">
                <Inbox className="size-4 shrink-0" aria-hidden />
                No alerts yet
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {feed.rows.map((row) => {
                  const tone = severityTone(row.severity)
                  const Icon = SEVERITY_ICON[row.severity] ?? Info
                  const isUnread = row.read_at === null
                  const className = cn(
                    'grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 px-3 py-2.5 text-left touch-manipulation transition-colors duration-[--dur-fast] hover:bg-surface-2',
                    FOCUS,
                    isUnread ? 'bg-blue-50/60' : '',
                  )
                  const inner = (
                    <>
                      <Icon className={cn('mt-0.5 size-4 shrink-0', SIGNALS[tone].text)} aria-hidden />
                      <span className="min-w-0">
                        <span
                          className={cn(
                            'block text-pretty text-sm leading-snug',
                            isUnread ? 'font-semibold text-foreground' : 'text-foreground',
                          )}
                        >
                          {row.title}
                        </span>
                        <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                          {row.body}
                        </span>
                        <span className="sr-only">
                          {severityLabel(row.severity)}. {isUnread ? 'Unread' : 'Read'}.
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {isUnread ? (
                          <span aria-hidden className="size-1.5 rounded-full bg-primary" />
                        ) : null}
                        <Figure className="text-[0.68rem] text-muted-foreground">
                          {relativeAge(row.created_at, now)}
                        </Figure>
                      </span>
                    </>
                  )
                  return (
                    <li key={row.id}>
                      {/* A row that goes somewhere is a **link**, not a button with a
                          `router.push` in it. `href` in the contract is an in-app path,
                          and a button throws away middle-click, ⌘-click, "open in new
                          tab" and the status bar preview — on the one surface where a
                          person most wants to peek at a job without leaving what they
                          are doing. A modified click is left to the browser and does not
                          mark the row read: they have not read it yet. */}
                      {row.href !== null && row.href !== '' ? (
                        <Link
                          href={row.href}
                          className={className}
                          onClick={(event) => {
                            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                              return
                            }
                            if (isUnread) feed.markRead(row.id)
                            setOpen(false)
                          }}
                        >
                          {inner}
                        </Link>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            if (isUnread) feed.markRead(row.id)
                          }}
                          className={className}
                        >
                          {inner}
                        </button>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </div>

          {/* The sound, and the only thing said about it. Off unless asked for; never
              while the tab is hidden or reduced motion is set (see `chime.ts`). */}
          <div className="flex items-center justify-between gap-2 border-t border-border bg-surface-2/60 px-3 py-2">
            <button
              type="button"
              role="switch"
              aria-checked={feed.sound}
              onClick={() => feed.setSound(!feed.sound)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface hover:text-foreground',
                FOCUS,
              )}
            >
              {feed.sound ? (
                <Volume2 className="size-3.5" aria-hidden />
              ) : (
                <VolumeX className="size-3.5" aria-hidden />
              )}
              Sound
            </button>
            <Figure className="text-[0.68rem] text-muted-foreground">
              {unread} unread
            </Figure>
          </div>
        </div>
      ) : null}
    </div>
  )
}
