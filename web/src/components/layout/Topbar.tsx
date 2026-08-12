'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { LogOut, UserRound } from 'lucide-react'
import { portalLabelFor, SECTIONS, type Role } from '@/lib/portal'
import { useAuth } from '@/lib/auth/AuthContext'
import { NotificationBell } from './NotificationBell'

/**
 * Topbar — TailAdmin's header restyled to our tokens: a breadcrumb (portal ›
 * section) on the left, and a present toggle + user chip on the right. The user
 * chip reflects the real signed-in session (username + role); sign-out clears the
 * session and returns to `/login`. The present button is a placeholder hook for
 * projector mode (wired later).
 */
export function Topbar({ role }: { role: Role }) {
  const pathname = usePathname()
  const router = useRouter()
  const { session, signOut } = useAuth()
  const sectionId = pathname.split('/')[3] ?? ''
  const sectionLabel = SECTIONS[sectionId]?.label ?? portalLabelFor(role)
  const displayName = session?.username ?? role.replace('_', ' ')
  const handleSignOut = (): void => {
    signOut()
    router.replace('/login')
  }
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-border bg-surface/80 px-5 backdrop-blur md:px-8">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm">
        <Link href={`/app/${role}`} className="text-muted-foreground hover:text-foreground">
          {portalLabelFor(role)}
        </Link>
        <span aria-hidden className="text-muted-foreground/50">
          /
        </span>
        <span className="font-medium text-foreground">{sectionLabel}</span>
      </nav>

      {/* Right cluster */}
      <div className="flex items-center gap-2">
        <NotificationBell />
        <div
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-foreground"
          title={`${displayName} · ${portalLabelFor(role)}`}
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-surface-2 text-muted-foreground">
            <UserRound className="size-4" />
          </span>
          <span className="hidden flex-col leading-tight sm:flex">
            <span className="truncate font-medium capitalize">{displayName}</span>
            <span className="truncate font-mono text-[0.62rem] tracking-wide text-muted-foreground/70">
              {role.replace('_', ' ')}
            </span>
          </span>
        </div>
        <button
          type="button"
          onClick={handleSignOut}
          title="Sign out"
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          <LogOut className="size-4" />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </div>
    </header>
  )
}
