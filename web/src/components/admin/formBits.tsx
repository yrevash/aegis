'use client'

import { AlertTriangle, Check, Lock } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { Input } from '@/components/primitives/input'
import { cn } from '@/lib/utils'

/**
 * The parts every admin write form is built from, so the three of them cannot
 * disagree about what a label, a refusal or a confirmation looks like.
 *
 * Two of these carry a rule rather than a style:
 *
 * - {@link Outcome} renders the **server's own sentence** on a refusal. Phase 6's
 *   audit found "something went wrong" standing in front of `A tenant-admin may only
 *   create users in its own tenant.` — the isolation rule refusing a cross-tenant
 *   write is the product working, and hiding it reads as the console being broken.
 * - {@link NotYours} is how a control the caller may not use appears: present, named,
 *   and captioned with why it is not theirs. A missing control teaches nothing; a
 *   control that posts and 403s teaches the wrong thing.
 */

/** One labelled control with its optional problem sentence. */
export function Field({
  id,
  label,
  hint,
  problem,
  children,
  className,
}: {
  id: string
  label: string
  /** What the field is for, when the label alone does not say it. */
  hint?: string
  /** The one sentence saying what is wrong, or undefined when nothing is. */
  problem?: string
  children: ReactNode
  className?: string
}): ReactElement {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-[0.78rem] font-medium text-foreground">
        {label}
      </label>
      {children}
      {problem != null ? (
        <p id={`${id}-problem`} className="text-[0.72rem] text-risk-ink">
          {problem}
        </p>
      ) : hint != null ? (
        <p id={`${id}-hint`} className="text-[0.72rem] text-muted-foreground">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

/** A text / number input already wired to its label and its problem sentence. */
export function TextField({
  id,
  label,
  hint,
  problem,
  className,
  ...input
}: {
  id: string
  label: string
  hint?: string
  problem?: string
  className?: string
} & React.ComponentProps<'input'>): ReactElement {
  return (
    <Field id={id} label={label} hint={hint} problem={problem} className={className}>
      <Input
        id={id}
        aria-invalid={problem != null}
        aria-describedby={problem != null ? `${id}-problem` : hint != null ? `${id}-hint` : undefined}
        {...input}
      />
    </Field>
  )
}

/** A `select` styled to match {@link TextField}, wired to its label the same way. */
export function SelectField({
  id,
  label,
  hint,
  problem,
  className,
  children,
  ...select
}: {
  id: string
  label: string
  hint?: string
  problem?: string
  className?: string
  children: ReactNode
} & React.ComponentProps<'select'>): ReactElement {
  return (
    <Field id={id} label={label} hint={hint} problem={problem} className={className}>
      <select
        id={id}
        aria-invalid={problem != null}
        aria-describedby={problem != null ? `${id}-problem` : hint != null ? `${id}-hint` : undefined}
        className={cn(
          'h-9 w-full rounded-lg border border-input bg-surface px-2.5 text-sm text-foreground shadow-xs transition-colors outline-none',
          'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40',
          'disabled:cursor-not-allowed disabled:opacity-50',
        )}
        {...select}
      >
        {children}
      </select>
    </Field>
  )
}

/** What a form says after it posted: what it created, or why the server refused. */
export type FormOutcome =
  | { kind: 'created'; message: string }
  | { kind: 'refused'; message: string }
  | null

/**
 * The strip under a form saying what just happened.
 *
 * A confirmation names the thing that now exists — "Created. Acme is tenant #7" —
 * because "Submitted" is a statement about the form and the operator needs a
 * statement about the platform. A refusal renders the backend's sentence verbatim.
 */
export function Outcome({ outcome }: { outcome: FormOutcome }): ReactElement | null {
  if (outcome === null) return null
  const refused = outcome.kind === 'refused'
  return (
    <div
      role={refused ? 'alert' : 'status'}
      className={cn(
        'flex items-start gap-2 rounded-lg border px-3 py-2 text-[0.78rem]',
        refused ? 'border-risk/40 bg-risk/5 text-risk-ink' : 'border-ok/40 bg-ok/5 text-ok-ink',
      )}
    >
      {refused ? (
        <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
      ) : (
        <Check className="mt-px size-3.5 shrink-0" aria-hidden />
      )}
      <span>{outcome.message}</span>
    </div>
  )
}

/**
 * A control this sign-in may not use, shown rather than hidden.
 *
 * §7.16's meta-rule is that the server enforces and the UI reflects. Reflecting it
 * means naming the control and saying whose it is — the operator learns the boundary
 * instead of wondering whether the console forgot a feature.
 */
export function NotYours({ label, reason }: { label: string; reason: string }): ReactElement {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-dashed border-border bg-surface-2/50 px-3 py-2.5">
      <Lock className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="flex flex-col gap-0.5">
        <span className="text-[0.78rem] font-medium text-foreground">{label}</span>
        <span className="text-[0.72rem] text-muted-foreground">{reason}</span>
      </div>
    </div>
  )
}
