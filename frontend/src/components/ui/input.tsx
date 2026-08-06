import type * as React from 'react'

import { cn } from '@/lib/utils'

/** A single-line text input styled for the console surface. */
function Input({ className, type, ...props }: React.ComponentProps<'input'>): React.ReactElement {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'flex h-9 w-full min-w-0 rounded-lg border border-input bg-surface px-3 py-1 text-sm shadow-xs transition-colors outline-none',
        'placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground',
        'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Input }
