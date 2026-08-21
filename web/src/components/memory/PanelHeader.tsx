import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'

interface PanelHeaderProps {
  icon: LucideIcon
  /** 1–3 word title (§1.3) — no sentences, no jargon. */
  title: string
  /** Tint class for the icon chip background, e.g. "bg-blue-400/12". */
  tint?: string
  /** Ink class for the icon, e.g. "text-blue-600". */
  ink?: string
  /** Relocated prose / honest tech, surfaced via the ⓘ tooltip (§1.2). */
  info?: ReactNode
  /** Optional trailing slot (a count badge, a toggle). */
  right?: ReactNode
  /**
   * The heading level this panel's title occupies. `h3` by default: these panels
   * sit inside cards under a page `h1` and a section `h2`. It was a bare `<span>`,
   * so a reader navigating by heading found a card with no entry at all.
   */
  as?: 'h2' | 'h3' | 'h4'
  className?: string
}

/**
 * The shared header for a Memory panel: a small icon chip, a short title, an
 * optional ⓘ (where the explanatory prose now lives), and a right-aligned slot.
 */
export function PanelHeader({
  icon: Icon,
  title,
  tint = 'bg-surface-2',
  ink = 'text-muted-foreground',
  info,
  right,
  as: Heading = 'h3',
  className,
}: PanelHeaderProps): ReactElement {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <span
        aria-hidden
        className={cn('grid size-6 shrink-0 place-items-center rounded-md', tint)}
      >
        <Icon className={cn('size-3.5', ink)} />
      </span>
      <Heading className="t-title min-w-0 text-pretty text-foreground">{title}</Heading>
      {info != null && <InfoTip label={`About ${title}`}>{info}</InfoTip>}
      {right != null && <span className="ml-auto flex items-center gap-2">{right}</span>}
    </div>
  )
}
