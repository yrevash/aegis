import { notFound } from 'next/navigation'
import { PortalGuard } from '@/components/auth/PortalGuard'
import { Sidebar } from '@/components/layout/Sidebar'
import { Topbar } from '@/components/layout/Topbar'
import { isRole } from '@/lib/portal'

/**
 * Portal shell — the shared chrome for a role's portal. Renders the Sidebar
 * (grouped nav) + Topbar (breadcrumb, present, user) around the active section.
 * Invalid roles 404. The active section is derived from the URL inside the
 * Sidebar/Topbar client components.
 *
 * The first thing in the tab order is a skip link, because the sidebar is eighteen
 * links deep and a keyboard user was tabbing past every one of them to reach the
 * question box — on every load, and again after every navigation.
 */

/** Where the skip link lands. Also the `<main>` the shell renders into. */
const MAIN_ID = 'main'
export default async function PortalLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: Promise<{ role: string }>
}) {
  const { role } = await params
  if (!isRole(role)) notFound()

  return (
    <PortalGuard role={role}>
      <div className="flex min-h-dvh">
        {/* Off-screen until focused, then a real, visible, clickable control. */}
        <a
          href={`#${MAIN_ID}`}
          className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:border focus:border-border focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-card focus:outline-none focus:ring-[3px] focus:ring-ring/50"
        >
          Skip to the main content
        </a>
        <Sidebar role={role} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar role={role} />
          <main id={MAIN_ID} tabIndex={-1} className="flex-1 px-5 py-6 md:px-8 md:py-8">
            <div className="animate-section mx-auto w-full max-w-7xl">{children}</div>
          </main>
        </div>
      </div>
    </PortalGuard>
  )
}
