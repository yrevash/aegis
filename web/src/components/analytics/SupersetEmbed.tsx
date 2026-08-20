'use client'

import { useEffect, useRef, useState, type ReactElement } from 'react'

import { analyticsMessage, getAnalyticsEmbedToken } from '@/lib/api/analytics'

/**
 * An embedded Superset dashboard, mounted inside the Aegis page.
 *
 * **The credential.** Superset's embedded SDK asks the host app for a *guest token*
 * whenever it needs one — on mount and again on refresh. That callback is the only
 * place a Superset credential exists in this browser, and what it returns is a
 * short-lived token minted by the Aegis backend, granting exactly one dashboard and
 * carrying the tenant's row-level filter. The Superset service account's JWT is not
 * here, is not reachable from here, and never crosses the wire: it would be the whole
 * BI instance, every tenant's rows included.
 *
 * **The theme.** An iframe brings its own CSS, and a dark Superset panel dropped into a
 * light product is exactly the kind of seam this console does not have anywhere else.
 * So it is handled rather than tolerated: `setThemeMode('default')` pins the embedded
 * dashboard to Superset's light theme on mount, and the chrome around it — title, tabs,
 * chart controls — is hidden, so what shows through the frame is the charts and not
 * another product's navigation. A Superset instance whose own `THEME_DEFAULT` has been
 * customised will still look like itself inside the frame; that is why the Aegis-drawn
 * charts, not this, are the default view of a board.
 *
 * **The failure.** `EMBEDDED_SUPERSET` and the guest-token mint are confirmed working on
 * Superset 6.1.0, so this is not a gamble — but three things still have to line up in
 * the browser (the token's `aud` claim, the dashboard's `allowed_domains`, and
 * `frame-ancestors`), and every one of them fails as a blank frame rather than an error.
 * So the SDK is loaded with a dynamic `import()` — a browser that never opens an
 * embeddable board never downloads it — and anything that goes wrong is caught *here*,
 * in this panel, with a sentence, on a page whose charts are read through a different
 * path and keep working.
 */
export function SupersetEmbed({
  boardId,
  title,
}: {
  boardId: string
  title: string
}): ReactElement {
  const mountPoint = useRef<HTMLDivElement | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let unmount: (() => void) | null = null
    let alive = true

    async function run(): Promise<void> {
      const node = mountPoint.current
      if (node === null) return
      try {
        // The first call happens before the SDK loads, so a backend that refuses the
        // grant reports *that* rather than a bundle error.
        const grant = await getAnalyticsEmbedToken(boardId)
        const { embedDashboard } = await import('@superset-ui/embedded-sdk')
        if (!alive) return
        const dashboard = await embedDashboard({
          id: grant.uuid,
          supersetDomain: grant.supersetDomain,
          mountPoint: node,
          iframeTitle: `${title} — Superset`,
          // Called again on refresh: every token this browser ever holds is minted by
          // Aegis, scoped to this session's tenant.
          fetchGuestToken: async () => (await getAnalyticsEmbedToken(boardId)).token,
          dashboardUiConfig: {
            hideTitle: true,
            hideTab: true,
            hideChartControls: false,
            filters: { visible: true, expanded: false },
          },
        })
        if (!alive) {
          dashboard.unmount()
          return
        }
        dashboard.setThemeMode('default')
        unmount = () => dashboard.unmount()
      } catch (err) {
        if (alive) setError(analyticsMessage(err))
      }
    }

    void run()
    return () => {
      alive = false
      unmount?.()
    }
  }, [boardId, title])

  if (error !== null) {
    return (
      <div
        role="status"
        className="rounded-lg border border-dashed border-border bg-surface-2/40 px-5 py-8 text-center"
      >
        <p className="text-sm font-medium text-foreground">The embedded dashboard did not load</p>
        <p className="mx-auto mt-1 max-w-xl text-sm text-muted-foreground">{error}</p>
        <p className="mt-2 text-sm text-muted-foreground">
          The charts above are read from the same Superset and do not need the embed.
        </p>
      </div>
    )
  }

  return (
    <div
      ref={mountPoint}
      // The SDK inserts its own iframe here; the height is ours so the frame does not
      // collapse to nothing while Superset is still painting.
      className="min-h-[640px] w-full overflow-hidden rounded-lg border border-border bg-card [&_iframe]:h-[640px] [&_iframe]:w-full [&_iframe]:border-0"
    />
  )
}
