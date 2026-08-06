import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { InfoTip } from '@/components/ui/InfoTip'
import { cn } from '@/lib/utils'

interface PanelHeadProps {
  icon: LucideIcon
  /** Soft-fill tint class for the icon chip, e.g. `bg-agent/12`. */
  tint: string
  /** Ink colour class for the icon, e.g. `text-agent-ink`. */
  ink: string
  /** 1–3 word panel title (§3). */
  title: string
  /** Honest one-line subtitle (§3.1) — the plain tech, always visible. */
  subtitle?: string
  /** Relocated prose, surfaced behind an ⓘ next to the title. */
  info?: ReactNode
  /** Right-aligned slot (a badge, count, or live dot). */
  right?: ReactNode
  className?: string
}

/**
 * The shared header for a card-less Improvement panel: an icon chip, a short
 * title with an optional honest subtitle, an ⓘ for the relocated technical
 * detail, and a right slot. Panels render this then their body so a `BentoTile`
 * owns the single surrounding Card (no double boxes).
 */
export function PanelHead({
  icon: Icon,
  tint,
  ink,
  title,
  subtitle,
  info,
  right,
  className,
}: PanelHeadProps): ReactElement {
  return (
    <div className={cn('mb-4 flex items-start gap-2.5', className)}>
      <span className={cn('mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg', tint)}>
        <Icon className={cn('size-4', ink)} />
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <h3 className="t-title text-foreground">{title}</h3>
          {info != null && <InfoTip label={`About ${title}`}>{info}</InfoTip>}
        </div>
        {subtitle && <p className="t-mono mt-0.5 truncate text-muted-foreground">{subtitle}</p>}
      </div>
      {right != null && <div className="ml-auto shrink-0 self-center">{right}</div>}
    </div>
  )
}
