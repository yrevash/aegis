import { LogOut, type LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { Logo } from '@/components/brand/Logo'
import { cn } from '@/lib/utils'

/** A navigation entry in the portal sidebar. */
export interface NavItem {
  id: string
  label: string
  icon: LucideIcon
  /** Short mono caption shown under the label (the honest tech, kept terse). */
  hint?: string
  /** Full honest subtitle surfaced on hover (native title). */
  tooltip?: string
  /** Optional group heading this item sits under (defaults to "Workspace"). */
  group?: string
}

interface SidebarProps {
  items: NavItem[]
  active: string
  onSelect: (id: string) => void
  /** Portal name shown at the rail foot (e.g. "Admin portal"). */
  portalLabel: string
  /** Optional sign-out handler; renders a logout row in the footer when set. */
  onSignOut?: () => void
  /** Render as an always-visible drawer (mobile) instead of the lg-only rail. */
  mobile?: boolean
}

const DEFAULT_GROUP = 'Workspace'

/** Preserve first-seen order while collecting items under their group heading. */
function groupItems(items: NavItem[]): [string, NavItem[]][] {
  const groups = new Map<string, NavItem[]>()
  for (const item of items) {
    const key = item.group ?? DEFAULT_GROUP
    const bucket = groups.get(key)
    if (bucket) bucket.push(item)
    else groups.set(key, [item])
  }
  return [...groups.entries()]
}

/** A single nav row — icon + label (+ optional hint), with an active accent. */
function NavRow({
  item,
  isActive,
  onSelect,
}: {
  item: NavItem
  isActive: boolean
  onSelect: (id: string) => void
}): ReactElement {
  const Icon = item.icon
  return (
    <button
      type="button"
      onClick={() => onSelect(item.id)}
      aria-current={isActive ? 'page' : undefined}
      className={cn(
        'group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
        isActive
          ? 'bg-surface-2 font-medium text-foreground'
          : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
      )}
      title={item.tooltip ?? item.label}
    >
      {/* Left accent bar marks the active surface, SnowUI-style. */}
      <span
        aria-hidden
        className={cn(
          'absolute top-1/2 left-0 h-4 -translate-y-1/2 rounded-r-full bg-primary transition-all',
          isActive ? 'w-1' : 'w-0',
        )}
      />
      <Icon className="size-[18px] shrink-0" />
      <span className="flex flex-col leading-tight">
        <span>{item.label}</span>
        {item.hint && (
          <span className="font-mono text-[0.62rem] tracking-wide text-muted-foreground/70">
            {item.hint}
          </span>
        )}
      </span>
    </button>
  )
}

/** The fixed left navigation rail: brand, grouped nav, portal label + logout. */
export function Sidebar({
  items,
  active,
  onSelect,
  portalLabel,
  onSignOut,
  mobile = false,
}: SidebarProps): ReactElement {
  const groups = groupItems(items)
  return (
    <nav
      aria-label="Primary"
      className={cn(
        'w-[244px] shrink-0 flex-col border-r border-border bg-card',
        mobile ? 'flex h-full' : 'hidden lg:flex',
      )}
    >
      {/* Brand lockup */}
      <div className="flex h-16 items-center gap-2.5 px-5">
        <Logo className="size-6" />
        <span className="leading-none">
          <span className="font-display text-[0.95rem] font-semibold tracking-tight text-foreground">
            Aegis
          </span>
          <span className="ml-1.5 font-mono text-[0.62rem] tracking-[0.22em] text-muted-foreground uppercase">
            Console
          </span>
        </span>
      </div>

      {/* Grouped navigation */}
      <div className="flex-1 space-y-5 overflow-y-auto px-3 py-2">
        {groups.map(([heading, groupItemsList]) => (
          <div key={heading} className="space-y-1">
            <span className="eyebrow block px-3 pb-1">{heading}</span>
            <ul className="space-y-1">
              {groupItemsList.map((item) => (
                <li key={item.id}>
                  <NavRow item={item} isActive={item.id === active} onSelect={onSelect} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* Footer: portal label + optional logout */}
      <div className="space-y-2 border-t border-border px-3 py-3">
        <span className="eyebrow block px-3">{portalLabel}</span>
        {onSignOut && (
          <button
            type="button"
            onClick={onSignOut}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
          >
            <LogOut className="size-[18px] shrink-0" />
            <span>Sign out</span>
          </button>
        )}
      </div>
    </nav>
  )
}
