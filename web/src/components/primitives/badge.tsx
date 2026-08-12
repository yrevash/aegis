'use client'

import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type * as React from 'react'

import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center justify-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 font-mono tracking-wide transition-colors',
  {
    variants: {
      // Signal variants: soft pastel fill + readable ink text (light-first).
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-border bg-surface-2 text-foreground',
        outline: 'border-border text-muted-foreground',
        agent: 'border-agent/50 bg-agent/12 text-agent-ink',
        graph: 'border-graph/50 bg-graph/12 text-graph-ink',
        risk: 'border-risk/60 bg-risk/15 text-risk-ink',
        block: 'border-block/60 bg-block/15 text-block-ink',
        ok: 'border-ok/60 bg-ok/15 text-ok-ink',
        ml: 'border-ml/50 bg-ml/12 text-ml-ink',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

/** A small status/label pill. Signal variants map to the trust taxonomy. */
function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<'span'> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }): React.ReactElement {
  const Comp = asChild ? Slot : 'span'
  return (
    <Comp data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}

export { Badge, badgeVariants }