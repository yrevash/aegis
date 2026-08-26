'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { LogOut, UserRound } from 'lucide-react'
import type { ReactElement } from 'react'

import { NotificationBell } from '@/components/notifications/NotificationBell'
import { TextSizeMenu } from '@/components/settings/TextSizeMenu'
import { portalLabelFor, SECTIONS, type Portal } from '@/lib/portal'
import { useAuth } from '@/lib/auth/AuthContext'

import { MobileNav } from './MobileNav'
import { activeSectionFrom } from './navGroups'

/**
 * The page chrome above every section: where you are, who you are, the way out.
 *
 * Three things changed with the shell pass.
 *
 * The **navigation trigger** now lives here, because below `lg` the rail is
 * hidden and this bar was the only chrome left on screen — see {@link MobileNav}
 * for what was actually missing.
 *
 * The **breadcrumb is a real list**. It was two spans and a slash glyph, which a
 * screen reader reads as one run-on line; an ordered list inside the labelled
 * `nav` is what makes "portal, then section" a structure rather than a
 * punctuation mark. The separator is `aria-hidden`, so it is seen and not heard.
 *
 * The **backdrop blur is gone**. A translucent blurred bar is glassmorphism, and
 * DESIGN.md §4 rules it out — but the practical reason is better: dense tabular
 * figures scrolling under a frosted panel are unreadable for the fraction of a
 * second they are half-under it. The bar is opaque now, and the content below it
 * simply passes behind.
 *
 * The notification bell that used to sit here was fed by nothing, so it answered
 * "you're all caught up" whatever the platform's actual state — a reassurance the
 * console had not earned. **There is a feed behind it now**, so it is back: it reads
 * `GET /notifications`, holds a live socket on `/notifications/stream`, and says which
 * of the two it is running on. When the backend does not serve those routes it renders
 * with no count and states that, which is the same honesty the deletion was about.
 *
 * The **text-size control** is here for an accessibility reason that only works if it is
 * here: its home is Settings, but the person who needs it is by definition on a screen
 * they are struggling to read, and making them navigate to a settings section first is
 * the failure. Both controls drive one shared step, so they cannot disagree.
 */
export function Topbar({ portal }: { portal: Portal }): ReactElement {
  const pathname = usePathname()
  const router = useRouter()
  const { session, signOut } = useAuth()
  const sectionId = activeSectionFrom(pathname)
  const sectionLabel = SECTIONS[sectionId]?.label ?? portalLabelFor(portal)
  const displayName = session?.username ?? portal.replace('_', ' ')
  const handleSignOut = (): void => {
    signOut()
    router.replace('/login')
  }

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-surface px-4 md:px-8">
      <MobileNav portal={portal} />

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex items-center gap-2 text-sm">
          {/*
           * The portal crumb is drawn from `lg`, where the rail is also visible; below
           * that the section name has the bar to itself.
           *
           * Two things were wrong here and they compounded. `truncate` is
           * `overflow: hidden`, which does nothing on an inline box, so this anchor never
           * truncated — it overran its `<li>` and the section label drew on top of it
           * ("Tenant admin po**Set…**"). And with both crumbs competing for a bar that
           * now also holds two more controls, 834px at the largest text step left
           * "Settings" as "Set…". The `<a>` is a block so it truncates when it has to,
           * and the crumb that gives way is the one the rail repeats — the section a
           * person is actually on is the half worth keeping whole.
           */}
          <li className="hidden min-w-0 lg:block">
            <Link
              href={`/app/${portal}`}
              className="block truncate rounded-sm text-muted-foreground outline-none transition-colors duration-[--dur-fast] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
            >
              {portalLabelFor(portal)}
            </Link>
          </li>
          <li aria-hidden className="hidden text-muted-foreground/50 lg:block">
            /
          </li>
          <li className="min-w-0">
            <span aria-current="page" className="block truncate font-medium text-foreground">
              {sectionLabel}
            </span>
          </li>
        </ol>
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        <TextSizeMenu />
        <NotificationBell />
        {/*
         * Who is signed in, said once for assistive tech at every width, and drawn only
         * from `sm` up.
         *
         * Below `sm` the chip was an avatar glyph carrying no text — and every control in
         * this bar is sized in `rem`, so at the largest text step five of them came to
         * 275px of a 390px bar and the breadcrumb truncated to "S.". The section a person
         * is looking at is worth more of that bar than a second glyph for an identity the
         * Sign out button beside it already implies, so the chip is what gives way. The
         * sentence stays.
         */}
        <p className="sr-only">
          Signed in as {displayName}, {portalLabelFor(portal)}
        </p>
        <p className="hidden items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-foreground sm:inline-flex">
          <span
            aria-hidden
            className="flex size-6 items-center justify-center rounded-md bg-surface-2 text-muted-foreground"
          >
            <UserRound className="size-4" aria-hidden />
          </span>
          <span className="flex min-w-0 flex-col leading-tight">
            <span className="truncate font-medium capitalize">{displayName}</span>
            <span className="truncate font-mono text-[0.6875rem] tracking-wide text-muted-foreground">
              {portal.replace('_', ' ')}
            </span>
          </span>
        </p>
        <button
          type="button"
          onClick={handleSignOut}
          className="inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-surface px-3 text-sm text-muted-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          <LogOut className="size-4" aria-hidden />
          <span className="hidden sm:inline">Sign out</span>
          <span className="sr-only sm:hidden">Sign out</span>
        </button>
      </div>
    </header>
  )
}
