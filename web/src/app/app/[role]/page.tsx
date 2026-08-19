import { notFound, redirect } from 'next/navigation'
import { defaultSectionFor, isPortal } from '@/lib/portal'

/** /app/[portal] → redirect to that portal's first (default) section. */
export default async function PortalIndex({ params }: { params: Promise<{ role: string }> }) {
  const { role: portal } = await params
  if (!isPortal(portal)) notFound()
  redirect(`/app/${portal}/${defaultSectionFor(portal)}`)
}
