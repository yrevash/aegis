import { Menu, MonitorPlay } from 'lucide-react'
import type { ReactElement } from 'react'

import type { Session } from '@/auth/AuthContext'
import { Badge } from '@/components/ui/badge'
import { useBackendMode } from '@/state/backendMode'

interface TopbarProps {
  session: Session
  /** The active section title, shown as the last breadcrumb crumb. */
  title: string
  /** Open the mobile navigation drawer (only rendered below `lg`). */
  onMenu?: () => void
  /** Enter projector/present mode. Shows a discoverable button when set. */
  onPresent?: () => void
}

/** Human name for the portal the session is scoped to. */
function portalName(role: Session['role']): string {
  switch (role) {
    case 'admin':
      return 'Admin portal'
    case 'ai_team':
      return 'AI team portal'
    case 'devops':
      return 'DevOps portal'
    case 'client':
      return 'Client portal'
  }
}

/**
 * The portal header: breadcrumb, global search, and the signed-in user.
 * Sign-out lives in the sidebar footer.
 */
export function Topbar({ session, title, onMenu, onPresent }: TopbarProps): ReactElement {
  const { mode } = useBackendMode()

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card/70 px-4 backdrop-blur lg:px-6">
      {/* Mobile nav trigger — the sidebar is hidden below lg, so this is the
          only way to switch sections on a small screen / projector. */}
      {onMenu && (
        <button
          type="button"
          onClick={onMenu}
          aria-label="Open navigation"
          className="grid size-9 shrink-0 place-items-center rounded-lg border border-border text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
        >
          <Menu className="size-5" />
        </button>
      )}

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
          <Badge variant="secondary" title="Running the in-browser scenario; no backend connected.">
            offline demo
          </Badge>
        )}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {/* Present mode — the projector view. Keyboard shortcut is F; the button
            makes it discoverable instead of a hidden hotkey. */}
        {onPresent && (
          <button
            type="button"
            onClick={onPresent}
            className="hidden items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-sm font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex"
            title="Enter present mode (or press F)"
          >
            <MonitorPlay className="size-4" />
            Present
            <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground">
              F
            </kbd>
          </button>
        )}

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
