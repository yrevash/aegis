import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

/** The Aegis mark — an aperture/shield motif drawn in the agent signal hue. */
export function Logo({ className }: { className?: string }): ReactElement {
  return (
    <svg
      viewBox="0 0 32 32"
      className={cn('size-7', className)}
      role="img"
      aria-label="Aegis Console"
      fill="none"
    >
      <path
        d="M16 2.5 L27 7 V15 C27 22.5 22 27.5 16 29.5 C10 27.5 5 22.5 5 15 V7 Z"
        stroke="var(--agent)"
        strokeWidth="1.6"
        fill="color-mix(in oklab, var(--agent) 8%, transparent)"
      />
      <circle cx="16" cy="15" r="4.4" stroke="var(--agent)" strokeWidth="1.6" />
      <circle cx="16" cy="15" r="1.4" fill="var(--agent)" />
      <path d="M16 4.6 V8 M16 22 V25.4 M6.8 15 H10 M22 15 H25.2" stroke="var(--graph)" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

/** The full wordmark used in the top bar. */
export function Wordmark({ className }: { className?: string }): ReactElement {
  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <Logo />
      <div className="leading-none">
        <span className="font-display text-[0.95rem] font-semibold tracking-tight text-foreground">
          AEGIS
        </span>
        <span className="ml-1.5 font-mono text-[0.62rem] tracking-[0.24em] text-muted-foreground uppercase">
          Console
        </span>
      </div>
    </div>
  )
}
