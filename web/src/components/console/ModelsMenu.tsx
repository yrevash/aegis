'use client'

import { Cpu } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getModels, type ModelsResponse } from '@/lib/api/console'
import { cn } from '@/lib/utils'

import { ComposerMenu } from './ComposerMenu'
import { modelButtonLabel, priceClauses, roleLabel } from './composerPricing'

/**
 * The composer's model control — the effective routing table, priced.
 *
 * `GET /models` is a projection over the gateway's own `routing_table()`, so this panel
 * shows what the gateway *would actually do* rather than a parallel list that could
 * disagree with it. Each row is priced in its own billing unit, because they do not
 * share one: a transcription role bills per minute of audio and an image role per
 * image, and a single "per 1k tokens" column would print a per-minute rate under a
 * per-token heading for a third of the table.
 *
 * **It reports; it does not set.** A per-user model preference is `agent.model` in the
 * settings catalogue and resolves platform → tenant → user. That resolver now *does*
 * have an HTTP surface — `GET|PUT /settings/{key}`, which this docstring used to say
 * was missing — and the Settings screen writes it there, with the `source` badge that
 * says which scope decided. What is deliberately not duplicated here is that control:
 * a second place to set the same key, sitting in a composer menu with no room for the
 * "who decided" badge, would be the ambiguity that screen exists to remove. This menu
 * answers the question the composer actually raises — *what will this run cost?* — from
 * the gateway's own table.
 */
export function ModelsMenu({ token }: { token: string | null }): ReactElement {
  const [models, setModels] = useState<ModelsResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    void getModels(token)
      .then((data) => {
        if (alive) setModels(data)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [token])

  const rows = models?.rows ?? null
  const label = failed ? 'Not readable' : modelButtonLabel(rows, models?.default_role ?? '')

  return (
    <ComposerMenu
      label="Model"
      value={label}
      icon={<Cpu aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />}
    >
      <p className="eyebrow">Effective routing</p>
      <p className="mt-1 text-[0.74rem] leading-snug text-muted-foreground">
        What the gateway routes each role to right now, priced in the unit that role bills
        in. Read from the gateway&apos;s own table, so it cannot disagree with a charge.
      </p>

      {failed && (
        <p className="mt-3 text-[0.78rem] text-muted-foreground">
          The routing table could not be read with this sign-in.
        </p>
      )}

      {!failed && rows === null && (
        <p className="mt-3 text-[0.78rem] text-muted-foreground">Reading the routing table…</p>
      )}

      {rows !== null && rows.length === 0 && (
        <p className="mt-3 text-[0.78rem] text-muted-foreground">
          The gateway reports no roles.
        </p>
      )}

      {rows !== null && rows.length > 0 && (
        <ul className="mt-3 flex max-h-72 flex-col gap-2 overflow-y-auto">
          {rows.map((row) => (
            <li
              key={row.role}
              className={cn(
                'rounded-lg border border-border/70 px-2.5 py-2',
                row.role === models?.default_role ? 'bg-surface-2/60' : 'bg-transparent',
              )}
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-[0.8rem] font-medium text-foreground">
                  {roleLabel(row.role)}
                </span>
                {row.role === models?.default_role && (
                  <span className="eyebrow shrink-0">Default answer</span>
                )}
              </div>
              <p className="truncate font-mono text-[0.72rem] text-muted-foreground" title={row.model}>
                {row.model}
                {row.small && ' · small'}
              </p>
              <p className="tabular mt-0.5 font-mono text-[0.7rem] text-muted-foreground">
                {priceClauses(row).join(' · ')}
              </p>
            </li>
          ))}
        </ul>
      )}
    </ComposerMenu>
  )
}
