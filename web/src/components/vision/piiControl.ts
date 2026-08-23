/**
 * Whether the image-PII control ran at all on an analysis.
 *
 * Pure and JSX-free so `tests/vision/piiControl.test.mjs` can run it directly.
 *
 * `pii_regions: []` has two meanings and the screen was reading only one of them. On a
 * run the injection screen refused, the control ladder printed *"Image PII — did not
 * run — not reached, injection_screen refused first"*, and the tile beside it printed
 * **"PII regions found 0"** with *"The image-PII control found no regions."* under it.
 * A control that did not run found nothing; that is not zero, and the empty array is
 * the control's silence rather than its answer.
 */

import type { VisionControlReport } from '@/lib/api/types'

/**
 * True only when something actually inspected the image for PII.
 *
 * The ladder already distinguishes the two absences — `not_run` is a control nobody
 * reached, `failed_closed` is one that was supposed to run and didn't, so the result
 * was refused rather than passed — and neither looked. A ladder with no `image_pii`
 * row at all is the same silence: nothing reported inspecting anything.
 */
export function piiControlRan(controls: VisionControlReport[] | null): boolean {
  const row = (controls ?? []).find((c) => c.stage === 'image_pii')
  if (row === undefined) return false
  return row.outcome !== 'not_run' && row.outcome !== 'failed_closed'
}
