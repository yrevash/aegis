'use client'

import { useEffect, useId, useRef, useState } from 'react'

/**
 * Render a Mermaid diagram to inline SVG.
 *
 * Mermaid is imported **lazily, inside the effect**, so its ~1 MB bundle is
 * fetched only by the page that actually draws a diagram and never lands in the
 * console's shared chunks.
 *
 * Themed to the console's light palette explicitly rather than by Mermaid's
 * default, so the diagram sits in the page instead of looking pasted into it.
 */
export function Mermaid({ chart, className }: { chart: string; className?: string }) {
  const [svg, setSvg] = useState<string | null>(null)
  const id = useId().replace(/:/g, '')
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    void (async () => {
      const mermaid = (await import('mermaid')).default
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
        themeVariables: {
          background: '#ffffff',
          primaryColor: '#ffffff',
          primaryTextColor: '#101828',
          primaryBorderColor: '#e4e7ec',
          lineColor: '#98a2b3',
          secondaryColor: '#f9f9fa',
          tertiaryColor: '#f2f4f7',
          clusterBkg: '#f9f9fa',
          clusterBorder: '#e4e7ec',
          fontSize: '13px',
        },
        flowchart: { curve: 'basis', padding: 14, useMaxWidth: true },
      })
      try {
        const { svg } = await mermaid.render(`m-${id}`, chart)
        if (live) setSvg(svg)
      } catch {
        // A diagram that will not parse renders as nothing rather than as a
        // broken-syntax error box on a marketing page.
        if (live) setSvg(null)
      }
    })()
    return () => {
      live = false
    }
  }, [chart, id])

  return (
    <div
      ref={host}
      className={className}
      // eslint-disable-next-line react/no-danger -- mermaid output, rendered with securityLevel 'strict'
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    />
  )
}
