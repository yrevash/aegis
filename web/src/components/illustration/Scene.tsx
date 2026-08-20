import Image from 'next/image'
import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'

/**
 * One catalogued illustration, addressed by what it *means* rather than by filename.
 *
 * **Why this indirection exists.** `public/illustrations/CREDITS.md` maps each scene to
 * the surface it belongs on, and the rule it states is the one worth enforcing in code:
 * *use a scene only where it describes something the product really does.* A filename
 * import invites the opposite — a screen reaches for whichever picture looks nice. A
 * named purpose does not: there is no `decoration` key here, and adding one would be a
 * visible act rather than an accident.
 *
 * The scenes are Storyset's, recoloured onto the Aegis blue ramp. **No attribution is
 * rendered in the product** — the owner's decision for an unpublished demo, recorded in
 * `CREDITS.md`, which also records that shipping this publicly changes that.
 *
 * Every scene is `aria-hidden` with an empty `alt`. Each one sits beside text that
 * already says the same thing — an `EmptyState`'s title and body, a refusal's sentence —
 * so announcing it would say everything twice, and DESIGN.md §7 is explicit that a
 * picture is never the only carrier of information.
 *
 * `unoptimized` is required rather than preferred: Next's image optimizer refuses an SVG
 * unless `images.dangerouslyAllowSVG` is set (measured: the optimizer answers 400 for
 * these files while the raw path answers 200), and a vector has nothing to optimise.
 */

/** What a scene is *for*. Each maps to exactly one file in `public/illustrations`. */
export type SceneName =
  /** Nothing has been recorded yet — the honest empty state this product produces a lot of. */
  | 'empty'
  /** The caller is not allowed to see this. */
  | 'forbidden'
  /** Give the platform a document. */
  | 'upload'
  /** The rails, the posture, the controls. */
  | 'security'
  /** Evidence gathered against an adversary. */
  | 'redteam'
  /** Something being measured against a standard. */
  | 'testing'
  /** Money over time. */
  | 'growth'
  /** Somebody with a question, before they ask it. */
  | 'curious'

const SCENE: Record<SceneName, string> = {
  empty: '/illustrations/No data-rafiki.svg',
  forbidden: '/illustrations/401 Error Unauthorized-rafiki.svg',
  upload: '/illustrations/Upload-rafiki.svg',
  security: '/illustrations/Security-pana.svg',
  redteam: '/illustrations/forensic expert-pana.svg',
  testing: '/illustrations/software tester-amico.svg',
  growth: '/illustrations/Business growth-amico.svg',
  curious: '/illustrations/Curious-rafiki.svg',
}

/** Rendered width in px. `sm` suits a panel body, `md` a whole screen's empty state. */
const SIZE = { sm: 132, md: 200, lg: 268 } as const

interface SceneProps {
  name: SceneName
  size?: keyof typeof SIZE
  className?: string
}

/** A decorative, catalogued illustration. Never the only thing saying what it says. */
export function Scene({ name, size = 'sm', className }: SceneProps): ReactElement {
  const width = SIZE[size]
  return (
    <Image
      src={SCENE[name]}
      alt=""
      aria-hidden
      unoptimized
      width={width}
      height={Math.round(width * 0.82)}
      className={cn('h-auto select-none', className)}
      style={{ width }}
    />
  )
}

/**
 * A scene stacked over its own words — the shape an empty or refused panel takes.
 *
 * The children are the message, and they are what a screen reader gets. The picture is
 * above them and silent.
 */
export function SceneState({
  name,
  size = 'sm',
  className,
  children,
}: SceneProps & { children: React.ReactNode }): ReactElement {
  return (
    <div className={cn('flex flex-col items-center gap-3 py-2 text-center', className)}>
      <Scene name={name} size={size} />
      <div className="w-full">{children}</div>
    </div>
  )
}
