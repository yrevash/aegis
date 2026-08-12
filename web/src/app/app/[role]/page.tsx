import { notFound, redirect } from 'next/navigation'
import { defaultSectionFor, isRole } from '@/lib/portal'

/** /app/[role] → redirect to the role's first (default) section. */
export default async function PortalIndex({ params }: { params: Promise<{ role: string }> }) {
  const { role } = await params
  if (!isRole(role)) notFound()
  redirect(`/app/${role}/${defaultSectionFor(role)}`)
}
