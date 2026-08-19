/**
 * The assistant bot's eyes — where a pupil sits, given where the pointer is.
 *
 * Pure on purpose. The component writes the result into CSS custom properties inside a
 * rAF and never through React state, so this file holds every decision worth checking
 * and the render path holds none of them.
 */

/** A point in viewport pixels. */
export interface Point {
  x: number
  y: number
}

/** Pupils forward: the resting position, and where a run start puts them. */
export const PUPIL_CENTRE: Readonly<Point> = Object.freeze({ x: 0, y: 0 })

/** How far a pupil may leave centre, in SVG user units (1:1 with px at render size). */
export const PUPIL_RANGE = 2

/** Pointer distance at which the pupils reach full deflection, in px. */
export const PUPIL_FALLOFF = 260

/**
 * The pupil offset for a pointer at `pointer`, given the head's centre.
 *
 * Deflection grows with distance and clamps at {@link PUPIL_RANGE}, so a pointer resting
 * on the head reads as "looking at you" rather than snapping to an edge.
 */
export function pupilOffset(
  pointer: Point,
  centre: Point,
  range = PUPIL_RANGE,
  falloff = PUPIL_FALLOFF,
): Point {
  const dx = pointer.x - centre.x
  const dy = pointer.y - centre.y
  const distance = Math.hypot(dx, dy)
  if (!Number.isFinite(distance) || distance < 1e-6) return { x: 0, y: 0 }
  const reach = Math.min(distance / falloff, 1) * range
  return { x: (dx / distance) * reach, y: (dy / distance) * reach }
}

/** What decides whether the eyes follow anything at all. */
export interface EyeConditions {
  /** The viewer asked for reduced motion. */
  reducedMotion: boolean
  /** A run is streaming. */
  running: boolean
}

/**
 * Whether the eyes track the pointer.
 *
 * They stop for reduced motion, and they stop while a run streams: stillness reads as
 * working, and it keeps the bot from competing with the trace at the one moment the
 * trace is the thing to watch. When this is false no listener is attached — the pupils
 * are written to {@link PUPIL_CENTRE} once and left there.
 */
export function tracksPointer({ reducedMotion, running }: EyeConditions): boolean {
  return !reducedMotion && !running
}
