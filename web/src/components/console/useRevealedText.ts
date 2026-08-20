'use client'

/**
 * Reveal received text progressively, so the answer types out instead of landing.
 *
 * The pacing curve lives in {@link advanceReveal} and is tested there; this is the
 * React half — a `requestAnimationFrame` loop that integrates it against real elapsed
 * time and hands back a prefix of the text the wire has already delivered.
 *
 * Three behaviours are deliberate:
 *
 * - **It never shows a character the wire did not send.** The reveal is a prefix of
 *   `text`, always. Nothing is predicted, buffered ahead or invented.
 * - **A turn that was already finished when it mounted does not re-type.** Scrolling
 *   back through a thread, or a re-render, must not restart everybody's answers. Pass
 *   `live: false` for a settled turn and it renders whole, immediately.
 * - **`prefers-reduced-motion` reveals instantly.** A typing effect is motion; the
 *   text is the content, and the content is never withheld from somebody who asked
 *   for less movement (DESIGN.md §6).
 */

import { useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

import { advanceReveal } from './revealPace'

/** What the caller renders: the prefix so far, and whether more is still coming. */
export interface Revealed {
  /** The characters revealed so far — always a prefix of the text passed in. */
  text: string
  /** True while the reveal has not caught up with what the wire has sent. */
  typing: boolean
}

/**
 * Progressively reveal `text`.
 *
 * @param text - Everything received so far. Grows as chunks arrive.
 * @param live - Whether this text is still being produced. `false` renders it whole,
 *   which is what a settled or restored turn wants.
 */
export function useRevealedText(text: string, live: boolean): Revealed {
  const reduced = useReducedMotion() ?? false
  // Fractional progress, so a slow rate still accumulates between frames. Seeded on
  // the first render: a turn that was already finished starts fully revealed.
  const progress = useRef<number | null>(null)
  if (progress.current === null) progress.current = live ? 0 : text.length
  const [shown, setShown] = useState(() => (live ? 0 : text.length))

  useEffect(() => {
    if (reduced) {
      progress.current = text.length
      setShown(text.length)
      return
    }
    // A new run reuses the panel with a shorter string; resync rather than holding a
    // count that is now past the end.
    if ((progress.current ?? 0) > text.length) {
      progress.current = text.length
      setShown(text.length)
      return
    }
    if ((progress.current ?? 0) >= text.length) return

    let frame = 0
    let last = performance.now()
    const step = (now: number): void => {
      const next = advanceReveal(progress.current ?? 0, text.length, now - last)
      last = now
      progress.current = next
      setShown(Math.floor(next))
      if (next < text.length) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [text, reduced])

  const count = Math.min(shown, text.length)
  return { text: text.slice(0, count), typing: count < text.length }
}
