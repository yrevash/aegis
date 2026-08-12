import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Card — TailAdmin's rounded-2xl white panel, restyled to our tokens (surface +
 * hairline border + soft diffuse shadow-card, not TailAdmin's default). The
 * building block for every panel across the portals.
 */
export function Card({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-border bg-card text-card-foreground shadow-card',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** Card header — title (display face) + optional eyebrow/description + actions. */
export function CardHeader({
  title,
  eyebrow,
  description,
  actions,
  className,
}: {
  title: ReactNode
  eyebrow?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-start justify-between gap-4 px-5 pt-5 md:px-6 md:pt-6', className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
        <h3 className="t-title truncate text-foreground">{title}</h3>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  )
}

/** Card body — standard padding. */
export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('px-5 py-5 md:px-6 md:py-6', className)}>{children}</div>
}
