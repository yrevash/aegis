'use client'

import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useRef, type ReactElement, type ReactNode } from 'react'

/**
 * M1 — **Arrive**. The one new motion in the system, and the only one that is GSAP.
 *
 * ## Why this exists at all
 *
 * GSAP's job here is exactly one thing: **orchestration**. A staggered, interruptible,
 * properly-cleaned-up entrance across a group of sibling elements is the thing CSS
 * `animation-delay` does badly — every element needs its own delay written by hand,
 * nothing can be reverted, and a re-render restarts all of them at once. If a proposed
 * use of GSAP is not orchestration across siblings, it is a CSS transition and belongs
 * in `globals.css` with the other fourteen keyframes.
 *
 * ## The two halves of the pattern, both load-bearing
 *
 * **`useGSAP` solves StrictMode.** `next.config.mjs` sets `reactStrictMode: true`, so
 * React mounts, unmounts and remounts every component in development and effects run
 * twice. A bare `gsap.to()` inside `useEffect` leaves the first tween alive against a
 * detached element, and the two copies fight over the same inline `transform`. `useGSAP`
 * wraps the callback in a `gsap.context()` and reverts it on cleanup.
 *
 * **`gsap.matchMedia()` solves reduced motion, and nothing else can.** The global kill
 * switch in `globals.css` zeroes `animation-*` and `transition-*` on `*`. GSAP writes
 * `transform` straight into the element's inline `style` on every frame, so that rule is
 * not weak against it — it is *inapplicable*. Measured in Chromium under `reduce`: a
 * `@keyframes` element had already snapped to its 300px endpoint while a GSAP tween sat
 * mid-flight at 105px, exactly where it sat with reduced motion off.
 *
 * `matchMedia` is also better than reading a `useReducedMotion()` boolean once at mount,
 * because it **reverts every tween created inside the block when the query flips** — a
 * reader who turns reduced motion on mid-session gets the animation undone, not merely
 * not-restarted.
 *
 * ## The trap in the reduce branch
 *
 * The `reduce` branch **sets the final state**; it does not return early. CSS hides the
 * children before the first paint, so skipping the tween without setting the end state
 * leaves the content invisible for ever — an accessibility preference that blanks the
 * page. Arriving instantly is the correct reduced-motion behaviour, not arriving never.
 * (The CSS carries its own `reduce` override for the same reason, so the content is
 * visible even in the instant before this effect runs.)
 *
 * ## Where it may be used
 *
 * Page-level section groups and card grids, **on first mount only**. Never on a data
 * refresh, a filter change or a tab switch: a staggered entrance that replays every time
 * a poll returns is the fastest way to make a product look like a template. Table bodies,
 * forms, drawers, menus, settings screens and every governance figure get nothing —
 * `DESIGN.md` §6 bans animating a spend cap because it makes the number look approximate,
 * and that ban covers GSAP counters too.
 *
 * @param children - The siblings to stagger. Each direct child is a step.
 * @param className - Passed to the scope element; layout stays the caller's business.
 */
export function RevealGroup({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}): ReactElement {
  const scope = useRef<HTMLDivElement>(null)

  useGSAP(
    () => {
      const targets = scope.current?.children
      if (targets === undefined || targets.length === 0) return undefined

      const mm = gsap.matchMedia()
      mm.add(
        {
          reduce: '(prefers-reduced-motion: reduce)',
          ok: '(prefers-reduced-motion: no-preference)',
        },
        (context) => {
          const { reduce } = context.conditions as { reduce: boolean }
          if (reduce) {
            // Arrive, instantly. Not "do nothing" — see the docblock.
            gsap.set(targets, { opacity: 1, y: 0, clearProps: 'transform' })
            return
          }
          // Opacity 0 is already applied by CSS before the first paint (see
          // `arriveBoot.ts`); this only adds the offset the tween animates out of.
          // Setting the hidden state here instead is what produced a measured
          // content → blank → content flash of up to 1.7s on a throttled machine.
          gsap.set(targets, { y: 10 })
          gsap.to(targets, {
            opacity: 1,
            y: 0,
            duration: 0.32, // --dur-slow
            ease: 'power2.out', // the token curve; never a bounce on an operator screen
            stagger: 0.04,
            clearProps: 'transform',
          })
        },
      )
      return () => mm.revert()
    },
    // `scope` confines every selector and target to this subtree. Without it a group
    // could reach across the document and animate somebody else's children.
    { scope },
  )

  return (
    <div ref={scope} data-arrive className={className}>
      {children}
    </div>
  )
}
