'use client'

import type { ReactElement } from 'react'

import type { VisionImageFacts, VisionPIIRegion } from '@/lib/api/types'

/**
 * The uploaded image with every detected-PII region drawn over it.
 *
 * Regions arrive in the *source image's* pixel space, so each box is converted to
 * a percentage of the reported natural size and positioned against the rendered
 * element — which keeps the overlay correct at any width without JavaScript
 * measuring anything.
 *
 * When the backend reports no dimensions there is nothing to scale against, and
 * the overlay is omitted with a line saying so rather than drawn at a guessed
 * scale. A box in the wrong place is worse than no box.
 */
export function PIIOverlay({
  src,
  alt,
  facts,
  regions,
}: {
  src: string
  alt: string
  facts: VisionImageFacts | null
  regions: VisionPIIRegion[]
}): ReactElement {
  const width = facts?.width ?? 0
  const height = facts?.height ?? 0
  const scalable = width > 0 && height > 0

  return (
    <div className="space-y-2">
      <div className="relative overflow-hidden rounded-lg border border-border bg-surface-2/40">
        {/* eslint-disable-next-line @next/next/no-img-element -- a client-side data: URL, never a remote asset */}
        <img src={src} alt={alt} className="block h-auto w-full" />
        {scalable
          ? regions.map((r, i) => (
              <div
                key={`${r.entity_type}-${i}`}
                className="absolute rounded-[3px] border-2 border-[color:var(--danger)] bg-[color:var(--danger)]/20"
                style={{
                  left: `${(r.left / width) * 100}%`,
                  top: `${(r.top / height) * 100}%`,
                  width: `${(r.width / width) * 100}%`,
                  height: `${(r.height / height) * 100}%`,
                }}
              >
                <span className="absolute -top-0.5 left-0 -translate-y-full whitespace-nowrap rounded-sm bg-[color:var(--danger)] px-1 py-px font-mono text-[0.6rem] font-medium text-white">
                  {r.entity_type}
                  {r.score != null ? ` ${(r.score * 100).toFixed(0)}%` : ''}
                </span>
              </div>
            ))
          : null}
      </div>
      {regions.length === 0 ? (
        <p className="text-[0.72rem] text-muted-foreground">No PII regions to draw.</p>
      ) : !scalable ? (
        <p className="text-[0.72rem] text-muted-foreground">
          {regions.length} region{regions.length === 1 ? '' : 's'} detected, but the backend
          reported no image dimensions to scale them against — not drawn rather than drawn in the
          wrong place.
        </p>
      ) : (
        <p className="text-[0.72rem] text-muted-foreground">
          {regions.length} detected region{regions.length === 1 ? '' : 's'}, painted out before the
          image was sent to the model. Entity kinds only — the recognised values never leave the
          backend.
        </p>
      )}
    </div>
  )
}
