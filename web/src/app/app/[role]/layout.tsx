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
 */
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
        <Sidebar role={role} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar role={role} />
          <main className="flex-1 px-5 py-6 md:px-8 md:py-8">
            <div className="animate-section mx-auto w-full max-w-7xl">{children}</div>
          </main>
        </div>
      </div>
    </PortalGuard>
  )
}
