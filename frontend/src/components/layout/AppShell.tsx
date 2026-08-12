import { Minimize2 } from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'

import { useAuth } from '@/auth/AuthContext'

import { Sidebar, type NavItem } from './Sidebar'
import { Topbar } from './Topbar'

interface AppShellProps {
  nav: NavItem[]
  active: string
  onSelect: (id: string) => void
  title: string
  portalLabel: string
  children: ReactNode
  /** Projector/present mode: hides the chrome and enlarges the console. */
  presenting?: boolean
  /** Enter present mode (surfaces a button in the header + the F shortcut). */
  onEnterPresent?: () => void
  /** Exit handler for present mode (also bound to Esc/F in the portal). */
  onExitPresent?: () => void
}

// Present mode bumps type scale and darkens secondary ink for projector legibility.
const PRESENT_STYLE: CSSProperties = {
  fontSize: '1.07rem',
  ['--muted-foreground' as string]: '#475467',
}

/** The portal chrome: navigation rail + header + scrolling content region. */
export function AppShell({
  nav,
  active,
  onSelect,
  title,
  portalLabel,
  children,
  presenting = false,
  onEnterPresent,
  onExitPresent,
}: AppShellProps): ReactNode {
  const { session, signOut } = useAuth()
  const [navOpen, setNavOpen] = useState(false)
  if (session === null) return null

  // Present mode: strip the sidebar + topbar and maximise the console grid.
  if (presenting) {
    return (
      <div className="relative h-full overflow-y-auto bg-background" style={PRESENT_STYLE}>
        <main className="mx-auto min-h-full max-w-[1900px] p-6 lg:p-8">{children}</main>
        <button
          type="button"
          onClick={onExitPresent}
          className="fixed right-4 bottom-4 z-30 flex items-center gap-2 rounded-full border border-border bg-card/90 px-3.5 py-2 text-sm font-medium shadow-card backdrop-blur transition-colors hover:bg-surface-2"
          title="Exit present mode (Esc or F)"
        >
          <Minimize2 className="size-4" />
          Exit present
          <kbd className="rounded border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[0.62rem] text-muted-foreground">
            Esc
          </kbd>
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Desktop rail (hidden below lg). */}
      <Sidebar
        items={nav}
        active={active}
        onSelect={onSelect}
        portalLabel={portalLabel}
        onSignOut={signOut}
      />

      {/* Mobile / narrow-screen drawer — the only way to switch sections below
          lg. Backdrop dismisses; selecting a section closes it. */}
      {navOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
            className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
          />
          <div className="absolute inset-y-0 left-0 shadow-pop">
            <Sidebar
              items={nav}
              active={active}
              onSelect={(id) => {
                onSelect(id)
                setNavOpen(false)
              }}
              portalLabel={portalLabel}
              onSignOut={signOut}
              mobile
            />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar
          session={session}
          title={title}
          onMenu={() => setNavOpen(true)}
          onPresent={onEnterPresent}
        />
        <main className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto p-4 lg:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
