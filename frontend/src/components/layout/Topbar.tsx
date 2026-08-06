import { Bell, Moon, Search, Sun } from 'lucide-react'
import type { ReactElement } from 'react'

import type { Session } from '@/auth/AuthContext'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useBackendMode } from '@/state/backendMode'

import { useTheme } from './theme'

interface TopbarProps {
  session: Session
  /** The active section title, shown as the last breadcrumb crumb. */
  title: string
}

/** Human name for the portal the session is scoped to. */
function portalName(role: Session['role']): string {
  return role === 'admin' ? 'Admin portal' : 'User portal'
}

/**
 * The portal header: breadcrumb, global search, theme toggle, notifications,
 * and the signed-in user. Sign-out lives in the sidebar footer.
 */
export function Topbar({ session, title }: TopbarProps): ReactElement {
  const { theme, toggle } = useTheme()
  const { mode } = useBackendMode()
  const isDark = theme === 'dark'

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card/70 px-4 backdrop-blur lg:px-6">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-2">
        <span className="hidden text-sm text-muted-foreground sm:inline">
          {portalName(session.role)}
        </span>
        <span aria-hidden className="hidden text-muted-foreground/50 sm:inline">
          /
        </span>
        <h1 className="truncate text-sm font-semibold text-foreground">{title}</h1>
        {mode === 'mock' && (
          <Badge variant="block" title="Running the in-browser scenario; no backend connected.">
            offline demo
          </Badge>
        )}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {/* Global search — presentational placeholder for the demo. */}
        <div className="relative hidden md:block">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            type="search"
            aria-label="Search"
            placeholder="Search"
            className="h-9 w-44 pl-8 lg:w-56"
          />
        </div>

        {/* Theme toggle */}
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
          title={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </Button>

        {/* Notifications */}
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label="Notifications"
          title="Notifications"
        >
          <Bell className="size-4" />
          <span
            aria-hidden
            className="absolute top-2 right-2 size-1.5 rounded-full bg-graph ring-2 ring-card"
          />
        </Button>

        {/* Signed-in user */}
        <div className="ml-1 flex items-center gap-2.5">
          <div className="hidden text-right leading-tight sm:block">
            <p className="text-sm font-medium text-foreground">{session.username}</p>
            <p className="font-mono text-[0.62rem] tracking-wide text-muted-foreground uppercase">
              {session.role}
            </p>
          </div>
          <div className="grid size-8 place-items-center rounded-full border border-border bg-surface-2 font-display text-sm font-semibold text-foreground">
            {session.username.charAt(0).toUpperCase()}
          </div>
        </div>
      </div>
    </header>
  )
}
