'use client'

import { useEffect, useRef, type ReactElement } from 'react'

import { PUPIL_CENTRE, pupilOffset, tracksPointer } from './botEyes'
import { prefersReducedMotion } from './motion'

/**
 * The assistant's face, above the composer.
 *
 * A line-art robot head drawn from primitives — no artwork, no sprite, no dependency.
 * The pupils follow the pointer and nothing else moves: no head rotation, no body, no
 * trailing. On a run start it goes still with the pupils forward, and it starts tracking
 * again when the run ends.
 *
 * This is **not** the brand mark. {@link AegisMark} is the falcon and stays the product
 * mark in the sidebar and on login; this is the assistant's face at the point of
 * conversation — a company logo and an assistant avatar are two different things and the
 * console should not blur them.
 *
 * The pointer position is written into `--pupil-x` / `--pupil-y` on the `<svg>` inside a
 * rAF, so a pointer sweep costs one style write per frame and zero React renders.
 * `prefers-reduced-motion` skips the listener entirely rather than throttling it, and
 * the whole mark is `aria-hidden`: it says nothing a screen reader needs.
 */
export function AssistantBot({ running }: { running: boolean }): ReactElement {
  const headRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const head = headRef.current
    if (head === null) return

    const centre = (): void => {
      head.style.setProperty('--pupil-x', `${PUPIL_CENTRE.x}px`)
      head.style.setProperty('--pupil-y', `${PUPIL_CENTRE.y}px`)
    }

    if (!tracksPointer({ reducedMotion: prefersReducedMotion(), running })) {
      centre()
      return
    }

    let frame = 0
    let pointerX = 0
    let pointerY = 0

    const paint = (): void => {
      frame = 0
      const box = head.getBoundingClientRect()
      const offset = pupilOffset(
        { x: pointerX, y: pointerY },
        { x: box.left + box.width / 2, y: box.top + box.height / 2 },
      )
      head.style.setProperty('--pupil-x', `${offset.x.toFixed(2)}px`)
      head.style.setProperty('--pupil-y', `${offset.y.toFixed(2)}px`)
    }

    const onMove = (event: PointerEvent): void => {
      pointerX = event.clientX
      pointerY = event.clientY
      if (frame === 0) frame = requestAnimationFrame(paint)
    }

    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (frame !== 0) cancelAnimationFrame(frame)
      centre()
    }
  }, [running])

  const pupil = { transform: 'translate(var(--pupil-x, 0px), var(--pupil-y, 0px))' }

  return (
    <svg
      ref={headRef}
      aria-hidden
      focusable="false"
      width={40}
      height={40}
      viewBox="0 0 40 40"
      className="shrink-0 text-foreground"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* Antenna, with the one accent on the surface. */}
      <path d="M20 11V6.5" />
      <circle cx="20" cy="4.4" r="1.9" fill="var(--blue-200)" />

      {/* Ears — two side tabs. */}
      <rect x="4.6" y="18" width="3.4" height="6" rx="1.5" />
      <rect x="32" y="18" width="3.4" height="6" rx="1.5" />

      {/* Head. */}
      <rect x="8" y="11" width="24" height="21" rx="6" />

      {/* Eyes: an open ring each, with a filled pupil that follows the pointer. */}
      <circle cx="15" cy="19.4" r="3.1" />
      <circle cx="25" cy="19.4" r="3.1" />
      <circle cx="15" cy="19.4" r="1.25" fill="currentColor" stroke="none" style={pupil} />
      <circle cx="25" cy="19.4" r="1.25" fill="currentColor" stroke="none" style={pupil} />

      {/* Nose. */}
      <path d="M20 23.6l1.9 2.5h-3.8z" />

      {/* Smile. */}
      <path d="M15.6 28.1q4.4 2.9 8.8 0" />
    </svg>
  )
}
