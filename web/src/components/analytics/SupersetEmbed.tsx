'use client'

import { useEffect, useState, useRef, type ReactElement } from 'react'

import { Absence } from '@/components/primitives/Receipt'
import { analyticsMessage, getAnalyticsEmbedToken } from '@/lib/api/analytics'

/**
 * What this browser has been able to *verify* about the embed.
 *
 * Not "what Superset said" — an iframe from another origin cannot be read, so the only
 * honest states are the ones a measurement can distinguish.
 */
export type EmbedState =
  /** Mounted, still being measured. */
  | 'probing'
  /** Measured, and the frame reported content of its own. */
  | 'live'
  /** Measured, and the frame kept reporting an empty page. */
  | 'blank'
  /** Mounted, and never answered a single measurement. */
  | 'silent'
  /** Never mounted — the token mint or the SDK threw. */
  | 'failed'

/**
 * The height the frame is mounted at while it is being measured, and the floor the
 * measurement is read against.
 *
 * The probe is `getScrollSize()`, which returns the embedded page's own
 * `body.scrollHeight`. A Superset page that drew nothing reports exactly the frame's
 * height — `html, body { height: 100% }` and no content — so a frame mounted short
 * reports short. A dashboard that drew reports its content: this deployment's
 * `Aegis Operations` measures **3488px** in a 900px viewport, and Superset's smallest
 * layout unit is a 400px chart row, so nothing that renders can come in under this.
 */
const PROBE_PX = 240
/** Ceiling on the frame once it is live, so a tall board does not run the page to 10,000px. */
const MAX_PX = 1400
/** Floor once it is live: a dashboard reads as a dashboard, not as a letterbox. */
const MIN_LIVE_PX = 560
const PROBE_EVERY_MS = 500
/** How long the frame gets to draw something before the absence is stated. */
const PROBE_WINDOW_MS = 25_000
/** Per-call ceiling on the switchboard round-trip, so a dead guest cannot hang the poll. */
const RPC_TIMEOUT_MS = 4_000

/** The embedded page's own content height, or null when it did not answer. */
async function measureContent(dashboard: {
  getScrollSize: () => Promise<{ width: number; height: number }>
}): Promise<number | null> {
  try {
    const size = await Promise.race([
      dashboard.getScrollSize(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), RPC_TIMEOUT_MS)),
    ])
    return size === null ? null : size.height
  } catch {
    return null
  }
}

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
 * another product's navigation.
 *
 * **The blank frame, which is the failure this component exists for.** `embedDashboard`
 * resolves on the iframe's `load` event and nothing more, so *every* way the embed can
 * fail after that point — the token's `aud` claim, the dashboard's `allowed_domains`,
 * `frame-ancestors`, a layout Superset declines to serve the guest token — resolves as
 * a success and paints white. This screen shipped that way: ~900px of nothing under a
 * green badge, on the one product whose argument is that it says what it cannot show.
 *
 * A cross-origin frame cannot be read, so the frame is *measured* instead. It mounts at
 * {@link PROBE_PX} and `getScrollSize()` is polled: a page that drew nothing reports the
 * frame's own height back, a dashboard reports its content. The first reading above the
 * floor sizes the frame to what it holds; a window of readings at or below it is a
 * stated {@link Absence} in the space the dashboard would have occupied — and the
 * Aegis-drawn boards on the same screen, which read the same Superset through the query
 * API, are untouched by any of it.
 */
export function SupersetEmbed({
  boardId,
  title,
  onState,
}: {
  boardId: string
  title: string
  /** Reports what the frame turned out to be, so the section header cannot claim more. */
  onState?: (state: EmbedState) => void
}): ReactElement {
  const mountPoint = useRef<HTMLDivElement | null>(null)
  const [state, setState] = useState<EmbedState>('probing')
  const [detail, setDetail] = useState<string | null>(null)
  const [framePx, setFramePx] = useState(PROBE_PX)

  useEffect(() => {
    onState?.(state)
  }, [state, onState])

  useEffect(() => {
    let unmount: (() => void) | null = null
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined

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

        const deadline = Date.now() + PROBE_WINDOW_MS
        let drew = false
        let answered = false

        const poll = async (): Promise<void> => {
          if (!alive) return
          const content = await measureContent(dashboard)
          if (!alive) return
          if (content !== null) {
            answered = true
            if (content > PROBE_PX) {
              drew = true
              // Keep polling while live: charts arrive after the layout does, and the
              // frame follows what it actually holds rather than a number chosen here.
              setFramePx(Math.min(Math.max(content, MIN_LIVE_PX), MAX_PX))
              setState('live')
            }
          }
          if (Date.now() >= deadline) {
            if (!drew) {
              dashboard.unmount()
              unmount = null
              setState(answered ? 'blank' : 'silent')
            }
            return
          }
          timer = setTimeout(() => void poll(), PROBE_EVERY_MS)
        }

        timer = setTimeout(() => void poll(), PROBE_EVERY_MS)
      } catch (err) {
        if (alive) {
          setDetail(analyticsMessage(err))
          setState('failed')
        }
      }
    }

    void run()
    return () => {
      alive = false
      clearTimeout(timer)
      unmount?.()
    }
  }, [boardId, title])

  if (state === 'failed' || state === 'blank' || state === 'silent') {
    return (
      // A live region, because this replaces a frame that was already on the page:
      // the reader who cannot see the rectangle is the one who most needs telling.
      <div role="status">
        <Absence
        // The section heading above already names the board, so this names the thing
        // that is missing from it rather than repeating the title back.
          figure="The embedded dashboard"
          why={
            state === 'failed'
              ? (detail ?? 'The embed could not be opened.')
              : state === 'blank'
                ? `Superset held the frame open for ${PROBE_WINDOW_MS / 1000}s and drew nothing into it.`
                : `The frame loaded and then answered nothing for ${PROBE_WINDOW_MS / 1000}s.`
          }
          needed={
            state === 'failed'
              ? 'A guest token this Superset accepts for this dashboard.'
              : 'Superset must serve this dashboard’s layout to the tenant’s guest token.'
          }
        />
      </div>
    )
  }

  return (
    <div className="min-w-0">
      <div
        ref={mountPoint}
        // The SDK inserts its own iframe here. The height is measured, not assumed: short
        // while the frame is being probed, then whatever the dashboard reports it holds.
        style={{ height: `${framePx}px` }}
        className="w-full min-w-0 overflow-hidden rounded-lg border border-border bg-card [&_iframe]:h-full [&_iframe]:w-full [&_iframe]:border-0"
      />
      {state === 'probing' ? (
        <p role="status" className="mt-1.5 text-xs text-muted-foreground">
          Waiting for Superset to draw this dashboard…
        </p>
      ) : null}
    </div>
  )
}
