import { Toaster as Sonner, type ToasterProps } from 'sonner'
import type * as React from 'react'

import { useTheme } from '@/components/layout/theme'

/** App-wide toast host, themed to follow the active light/dark identity. */
function Toaster(props: ToasterProps): React.ReactElement {
  const { theme } = useTheme()
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      toastOptions={{
        style: {
          background: 'var(--popover)',
          border: '1px solid var(--border)',
          color: 'var(--foreground)',
          fontFamily: 'var(--font-sans)',
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
