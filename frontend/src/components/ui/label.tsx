import * as LabelPrimitive from '@radix-ui/react-label'
import type * as React from 'react'

import { cn } from '@/lib/utils'

/** An accessible form label associated with a control via `htmlFor`. */
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>): React.ReactElement {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        'flex items-center gap-2 text-sm font-medium select-none peer-disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}

export { Label }
