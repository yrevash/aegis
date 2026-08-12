'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ShieldHalf } from 'lucide-react'
import { cn } from '@/lib/utils'
import { portalLabelFor, sectionsFor, type Role, type Section } from '@/lib/portal'

const DEFAULT_GROUP = 'Workspace'

/** Preserve first-seen order while collecting sections under their group heading. */
function groupSections(sections: Section[]): [string, Section[]][] {
  const groups = new Map<string, Section[]>()
  for (const s of sections) {
    const key = s.group ?? DEFAULT_GROUP
    const bucket = groups.get(key)
    if (bucket) bucket.push(s)
    else groups.set(key, [s])
  }
  return [...groups.entries()]
}

/**
 * The fixed left navigation rail — TailAdmin's sidebar structure (brand, grouped
 * nav with uppercase group titles, active accent), restyled to our tokens. Each
 * row links to /app/[role]/[section]; the active row is derived from the URL.
 */
export function Sidebar({ role }: { role: Role }) {
  const pathname = usePathname()
  const active = pathname.split('/')[3] ?? ''
  const groups = groupSections(sectionsFor(role))

  return (
    <aside className="sticky top-0 hidden h-dvh w-[264px] shrink-0 self-start border-r border-border bg-surface lg:flex lg:flex-col">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 pt-7 pb-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <ShieldHalf className="size-5" />
        </span>
        <span className="text-[0.95rem] font-semibold tracking-tight text-foreground">Aegis</span>
      </div>

      {/* Grouped nav */}
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {groups.map(([heading, items]) => (
          <div key={heading} className="mb-5">
            <h3 className="mb-2 px-3 text-[0.68rem] font-medium uppercase tracking-[0.14em] text-muted-foreground/70">
              {heading}
            </h3>
            <ul className="space-y-1">
              {items.map((item) => {
                const Icon = item.icon
                const isActive = item.id === active
                return (
                  <li key={item.id}>
                    <Link
                      href={`/app/${role}/${item.id}`}
                      aria-current={isActive ? 'page' : undefined}
                      title={item.tooltip}
                      className={cn(
                        'group relative flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                        isActive
                          ? 'bg-surface-2 font-medium text-foreground'
                          : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                      )}
                    >
                      <span
                        aria-hidden
                        className={cn(
                          'absolute top-1/2 left-0 h-4 -translate-y-1/2 rounded-r-full bg-primary transition-all',
                          isActive ? 'w-1' : 'w-0',
                        )}
                      />
                      <Icon className="size-[18px] shrink-0" />
                      <span className="min-w-0 truncate">{item.label}</span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Portal label */}
      <div className="border-t border-border px-5 py-4">
        <p className="eyebrow">{portalLabelFor(role)}</p>
      </div>
    </aside>
  )
}
