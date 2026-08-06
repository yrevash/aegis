import type { ReactElement, ReactNode } from 'react'

/**
 * The human-approval spotlight. When the run pauses at the gate, this scrims
 * and blurs the whole console and lifts the {@link ApprovalCard} forward — the
 * one moment the demo asks a human to decide becomes the only thing on screen.
 * The scrim is inert to motion preferences (a static dim), and traps focus on
 * the decision by covering the dimmed surface behind it.
 */
export function ApprovalSpotlight({ children }: { children: ReactNode }): ReactElement {
  return (
    <div
      className="fixed inset-0 z-40 grid place-items-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Human approval required"
    >
      <div aria-hidden className="absolute inset-0 bg-foreground/30 backdrop-blur-[2px]" />
      <div className="animate-trace-in relative z-10 w-full max-w-md">{children}</div>
    </div>
  )
}
