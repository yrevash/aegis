'use client'

import { Info } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/primitives/tooltip'
import { TAP_TARGET } from '@/components/primitives/tapTarget'
import { cn } from '@/lib/utils'

interface InfoTipProps {
  /** The relocated prose / honest technical detail shown on hover + focus. */
  children: ReactNode
  /** Accessible label for the trigger (default "More information"). */
  label?: string
  className?: string
}

/**
 * The standard prose-relocation affordance (§2, §5): a small ⓘ trigger that
 * reveals an explanatory tooltip. The glass-box depth stays — it just lives one
 * layer down. `TooltipProvider` is mounted at the app root in
 * `components/auth/Providers.tsx` — this comment asserted that before it was
 * true, and Radix answers a missing provider by throwing, not by degrading.
 */
export function InfoTip({ children, label = 'More information', className }: InfoTipProps): ReactElement {
  return (
    <Tooltip>
      <TooltipTrigger
        type="button"
        aria-label={label}
        className={cn(
          'inline-grid size-4 place-items-center rounded-full text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          // The 1rem glyph is the drawing; the pointer target around it is 24px at
          // every text size. This is the console's commonest small control — a page
          // of them is a page of misses on the setting chosen to make things easier.
          TAP_TARGET,
          className,
        )}
      >
        <Info aria-hidden className="size-3.5" />
      </TooltipTrigger>
      <TooltipContent className="max-w-xs text-xs leading-relaxed">{children}</TooltipContent>
    </Tooltip>
  )
}