import Image from 'next/image'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

/**
 * An illustration on one of the pre-auth pages, addressed by what it depicts.
 *
 * **Why this is not `components/illustration/Scene`.** That component exists for
 * empty states and refusals inside the console, and it is deliberately
 * `aria-hidden` with an empty `alt`: it always sits directly beside text saying
 * the same thing, so announcing it would say everything twice. Here the scene is
 * an editorial image several hundred pixels wide with no adjacent sentence
 * describing it, so it gets a real alt that says what is in the picture. Same
 * assets, different job, different accessibility answer.
 *
 * Used by the landing page, the sign-in page and the 404 — the three surfaces a
 * visitor meets before a portal, which share one voice. The console's own screens
 * use `components/illustration/Scene` instead.
 *
 * **Every scene here is literal, and two candidates were rejected for not being.**
 * `CREDITS.md`'s rule is that a scene belongs only where it describes something
 * the product really does. `401 Error Unauthorized` has the string "401" drawn
 * into the artwork, and Aegis does not answer a cross-tenant question with a 401
 * — it answers with a run that says it cannot source anything — so the isolation
 * section carries no picture rather than a false status code in 200-point type.
 * Nothing in the set depicts *a screen that crashed*, so `error.tsx` carries none
 * either; a picture chosen for the mood of a page is the definition of stock art.
 * `people using robots` was drafted into the sign-in panel and then cut on sight:
 * rendered, it is a family on a sofa with a robot vacuum, which is a consumer
 * smart-home scene and not one frame of what this product does.
 *
 * **These only work on a light ground.** Each file carries its own near-white
 * background plate, so on `--rail` navy the scene reads as a pale rectangle
 * pasted onto the panel rather than as a drawing. Measured, not assumed — which
 * is why the sign-in page's left panel is the blue-tinted canvas rather than the
 * navy it was first drafted as.
 *
 * No attribution is rendered — `CREDITS.md` records the owner's decision for an
 * unpublished demo, and records that publishing changes it.
 *
 * `unoptimized` is required, not preferred: Next's optimizer answers 400 for an
 * SVG unless `images.dangerouslyAllowSVG` is set, and a vector has nothing to
 * optimise anyway.
 */

/** The scenes this page uses, by what they depict. */
const SCENE = {
  /** Three colleagues fitting puzzle pieces together — a team on one task. */
  team: {
    src: '/illustrations/forming team leadership-amico.svg',
    alt: 'Three colleagues fitting large puzzle pieces together, each holding a different piece of the same picture.',
    ratio: 1,
  },
  /** A signed consent form — authorising an action, on the record. */
  consent: {
    src: '/illustrations/Consent-rafiki.svg',
    alt: 'A clipboard holding a consent form, a pen beside it and a person signing at the marked line.',
    ratio: 1,
  },
  /** A locked screen — what a sign-in form is for. */
  locked: {
    src: '/illustrations/Security-pana.svg',
    alt: 'A person standing beside a large screen showing a closed padlock inside a shield.',
    ratio: 0.667,
  },
  /** A window with nothing in it — the shape of an address that serves nothing. */
  nothing: {
    src: '/illustrations/No data-rafiki.svg',
    alt: 'A person facing a browser window that holds a single empty document labelled no data.',
    ratio: 1,
  },
} as const

export type SceneKey = keyof typeof SCENE

interface LandingSceneProps {
  name: SceneKey
  /** Rendered width in CSS pixels at the largest breakpoint. */
  width: number
  className?: string
}

export function LandingScene({ name, width, className }: LandingSceneProps): ReactElement {
  const scene = SCENE[name]
  return (
    <Image
      src={scene.src}
      alt={scene.alt}
      unoptimized
      width={width}
      height={Math.round(width * scene.ratio)}
      className={cn('h-auto w-full max-w-full select-none', className)}
      style={{ maxWidth: width }}
    />
  )
}
