/**
 * Render smoke test for the Roles & Access surface.
 *
 * Runs in a plain Node environment (no jsdom), so the component is exercised with
 * `renderToStaticMarkup`. Effects do not run under static rendering, so the load
 * effect never fires and the surface renders its honest initial loading state —
 * which is exactly what we assert. Mounting without throwing proves the wiring
 * (useAuth context, InfoTip tooltip, header) is sound; the interactive paths are
 * covered by the pure logic in `roleCatalog.test.ts`.
 */

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AuthProvider } from '@/auth/AuthContext'
import { TooltipProvider } from '@/components/ui/tooltip'

import { RolesAccess } from './RolesAccess'

describe('RolesAccess (render smoke)', () => {
  it('mounts and shows the header + honest loading state', () => {
    const html = renderToStaticMarkup(
      createElement(
        AuthProvider,
        null,
        createElement(
          TooltipProvider,
          null,
          createElement(RolesAccess, { token: null }),
        ),
      ),
    )
    expect(html).toContain('Roles')
    expect(html).toContain('RBAC')
    expect(html).toContain('Loading users')
  })
})
