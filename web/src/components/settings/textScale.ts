/**
 * Text size — the whole mechanism, with no React in it.
 *
 * The console's type is entirely rem-based: `globals.css` sets no root font-size and
 * declares no `px` font-size anywhere, and the Tailwind scale it builds on is rem. So
 * one property on `<html>` scales every word in the product proportionally — headings,
 * table cells, badges, the mono figures — and nothing has to be restyled per step. That
 * is the whole reason this is four lines of state and not a second type system.
 *
 * **Discrete steps, not a slider.** A slider produces 103%, and 103% produces a layout
 * nobody has ever looked at. Four steps are four layouts, each of which can be checked
 * at 390 / 834 / 1440 / 1920 and fixed if it breaks.
 *
 * **Applied before first paint.** {@link TEXT_SCALE_BOOT} runs synchronously in `<head>`,
 * so a person who set 125% never sees the page at 100% first. A `useEffect` cannot do
 * this — it runs after the first paint, which is precisely the flash.
 *
 * Everything here is pure and takes its storage and its element as arguments, which is
 * what lets `tests/settings/textScale.test.mjs` exercise it directly under Node.
 */

/** One step on the scale. */
export interface TextScaleStep {
  /** Percentage applied to the root font size. */
  percent: number
  /** The word for it. Short — this is a segmented control, not a menu. */
  label: string
}

/**
 * The steps.
 *
 * The top step is **125%**, verified rather than assumed: at 125% every route was
 * measured at 390 / 834 / 1440 / 1920 for horizontal overflow of the document. Going
 * further (150%, which some systems offer) is a different exercise — it starts breaking
 * fixed-width table columns — and a top step that overflows is worse than one that stops
 * lower, because the person who needs it is the one who cannot read the result.
 */
export const TEXT_SCALES: readonly TextScaleStep[] = [
  { percent: 90, label: 'Small' },
  { percent: 100, label: 'Default' },
  { percent: 110, label: 'Large' },
  { percent: 125, label: 'Largest' },
] as const

/** The step in force when nobody has chosen one. */
export const DEFAULT_TEXT_SCALE = 100

/** Where the choice lives. Per browser, like every other viewer preference. */
export const TEXT_SCALE_KEY = 'aegis.textScale'

/** Every percentage the app will apply. Anything else is not a step. */
export const TEXT_SCALE_PERCENTS: readonly number[] = TEXT_SCALES.map((step) => step.percent)

/**
 * The nearest legal step for anything at all — a string from storage, a number, junk,
 * `null`. Never throws, and never returns a value that is not on {@link TEXT_SCALES}.
 */
export function normaliseScale(raw: unknown): number {
  const value = typeof raw === 'string' ? Number.parseInt(raw, 10) : raw
  if (typeof value !== 'number' || !Number.isFinite(value)) return DEFAULT_TEXT_SCALE
  if (TEXT_SCALE_PERCENTS.includes(value)) return value
  // A percentage from an older build, or a hand-edited storage value: snap it rather
  // than discard it, so somebody who set 120% lands on 125% and not back on 100%.
  return TEXT_SCALE_PERCENTS.reduce((best, step) =>
    Math.abs(step - value) < Math.abs(best - value) ? step : best,
  )
}

/** The stored step, or the default when there is nothing readable. */
export function readStoredScale(storage: Pick<Storage, 'getItem'> | null): number {
  try {
    const raw = storage?.getItem(TEXT_SCALE_KEY) ?? null
    return raw === null ? DEFAULT_TEXT_SCALE : normaliseScale(raw)
  } catch {
    // Private mode, or storage disabled by policy. The default is not a failure.
    return DEFAULT_TEXT_SCALE
  }
}

/** Remember the step. A storage that refuses the write still leaves it applied. */
export function persistScale(storage: Pick<Storage, 'setItem'> | null, percent: number): void {
  try {
    storage?.setItem(TEXT_SCALE_KEY, String(normaliseScale(percent)))
  } catch {
    /* the choice holds for this tab; nothing here is worth an error state */
  }
}

/**
 * Put the step in force on the document element.
 *
 * 100% clears the property rather than setting `font-size: 100%`, so the default state
 * of the app is the browser's own root size — which is itself an accessibility setting,
 * and one this control must not overwrite for someone who has already raised it.
 */
export function applyScale(root: { style: { fontSize: string } } | null, percent: number): void {
  if (root === null) return
  const step = normaliseScale(percent)
  root.style.fontSize = step === DEFAULT_TEXT_SCALE ? '' : `${step}%`
}

/**
 * The pre-paint script, inlined in `<head>` by the root layout.
 *
 * It is a string, and it duplicates the key and the step list, because a `<script>` in
 * the document head cannot import a module — the whole point is that it runs before
 * anything is fetched. `tests/settings/textScale.test.mjs` asserts the duplication is
 * faithful: the test fails if the key or any step here stops matching the constants
 * above, which is the only way this drifts.
 */
export const TEXT_SCALE_BOOT = `(function(){try{var s=localStorage.getItem('${TEXT_SCALE_KEY}');if(!s)return;var n=parseInt(s,10);if([${TEXT_SCALE_PERCENTS.join(',')}].indexOf(n)>-1&&n!==${DEFAULT_TEXT_SCALE}){document.documentElement.style.fontSize=n+'%';}}catch(e){}})();`
