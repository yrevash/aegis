import Image from 'next/image'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

/**
 * An illustration on the public page, addressed by what it depicts.
 *
 * **Why this is not `components/illustration/Scene`.** That component exists for
 * empty states and refusals inside the console, and it is deliberately
 * `aria-hidden` with an empty `alt`: it always sits directly beside text saying
 * the same thing, so announcing it would say everything twice. Here the scene is
 * an editorial image several hundred pixels wide with no adjacent sentence
 * describing it, so it gets a real alt that says what is in the picture. Same
 * assets, different job, different accessibility answer.
 *
 * **Only two scenes ship on this page, and both are literal.** `CREDITS.md`'s
 * rule is that a scene belongs only where it describes something the product
 * really does, and the tempting third — `401 Error Unauthorized` for the
 * tenant-isolation section — has the string "401" drawn into the artwork. Aegis
 * does not answer a cross-tenant question with a 401; it answers with a run that
 * says it cannot source anything. Shipping that scene would have put a false
 * status code on the page in 200-point type, so that section carries no picture.
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
