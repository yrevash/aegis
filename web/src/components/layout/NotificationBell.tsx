'use client'

import { Bell, CheckCircle2 } from 'lucide-react'
import { useEffect, useRef, useState, type ReactElement } from 'react'

import { cn } from '@/lib/utils'

/** One notification row shown in the bell dropdown. */
export interface Notification {
  id: string
  title: string
  detail?: string
  tone?: 'info' | 'warning' | 'critical'
  ts?: string
}

const DOT: Record<NonNullable<Notification['tone']>, string> = {
  info: 'var(--muted-foreground, #64748b)',
  warning: 'var(--risk-ink, #b45309)',
  critical: 'var(--danger, #dc2626)',
}

/**
 * The top-bar notification bell — a live inbox for platform signals (approvals
 * waiting, budgets breached, guardrail blocks). Renders an unread dot when items
 * are present and a dropdown listing them. `items` is supplied by the shell; an
 * empty list shows an honest "all caught up" state.
 */
export function NotificationBell({ items = [] }: { items?: Notification[] }): ReactElement {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const unread = items.length

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent): void => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={unread > 0 ? `${unread} notifications` : 'Notifications'}
        aria-expanded={open}
        className="relative inline-flex size-9 items-center justify-center rounded-lg border border-border bg-surface text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 flex min-w-[1.05rem] items-center justify-center rounded-full bg-[color:var(--danger,#dc2626)] px-1 text-[0.62rem] font-semibold leading-4 text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 overflow-hidden rounded-xl border border-border bg-card shadow-pop">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <span className="text-sm font-medium text-foreground">Notifications</span>
            {unread > 0 && (
              <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[0.68rem] font-medium text-muted-foreground">
                {unread} new
              </span>
            )}
          </div>
          {unread === 0 ? (
            <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
              <CheckCircle2 className="size-5 text-ok-ink" />
              <span className="text-sm text-muted-foreground">You&apos;re all caught up.</span>
            </div>
          ) : (
            <ul className="max-h-80 overflow-y-auto">
              {items.map((n) => (
                <li key={n.id} className="flex items-start gap-2.5 border-b border-border/60 px-4 py-3 last:border-0">
                  <span
                    className="mt-1.5 size-2 shrink-0 rounded-full"
                    style={{ background: DOT[n.tone ?? 'info'] }}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground">{n.title}</p>
                    {n.detail && <p className="mt-0.5 text-[0.8rem] text-muted-foreground">{n.detail}</p>}
                  </div>
                  {n.ts && (
                    <span className={cn('shrink-0 font-mono text-[0.66rem] text-muted-foreground/70')}>{n.ts}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
