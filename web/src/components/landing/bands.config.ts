/**
 * The two removable bands on the public landing page, and the one switch each.
 *
 * ## Why this file exists
 *
 * The standards band names published frameworks — ISO/IEC 27001, SOC 2, the EU AI
 * Act, the DPDP Act — on a public marketing page. Naming a framework you have not
 * been audited against is defensible only while the framing stays exact, and framing
 * is the kind of thing that survives review and then loses an argument in a room. So
 * the band ships with its own off switch, owned by the project, requiring no code
 * archaeology and no judgement call at the moment somebody wants it gone.
 *
 * ## What flipping each switch off removes — exactly
 *
 * `STANDARDS_BAND` → `false` removes, from `web/src/app/page.tsx`, the entire
 * `<StandardsBand />` section:
 *
 * - the section heading, eyebrow and the "Certified against none" line;
 * - the "Compliance-readiness evidence — not certification" notice;
 * - the four-state coverage strip and its derived counts;
 * - every framework wordmark cell and its per-framework counts;
 * - the "What 'built to' means here" disclosure;
 * - the `GET /platform/standards` receipt;
 * - **and the fetch itself** — the endpoint is called from inside the band, so a
 *   disabled band makes no request and the network tab shows nothing.
 *
 * Nothing else on the page changes. There is no heading left behind, no empty grid,
 * no reserved space and no layout shift: the sections either side are self-contained
 * `<LandingSection>`s carrying their own vertical padding, so they simply close up.
 * Nothing else in the app imports `StandardsBand`, `standardsSummary` or
 * `lib/api/standards`, so no other screen is touched, and the endpoint stops being
 * called by anything at all.
 *
 * **One cosmetic consequence, stated rather than hidden.** With both bands on, the
 * page alternates ground colour correctly — `LivePlatform` white, this band on the
 * canvas, `StackBand` white, `Roadmap` on the canvas. Switch this band off and
 * `LivePlatform` meets `StackBand` white-on-white; switch the *other* one off and
 * this band meets `Roadmap` canvas-on-canvas. Neither is a defect — every section
 * carries 64–80px of its own padding, so the seam still reads as a seam — but the
 * alternation is a nicety that a removal spends, and two tones cannot survive an
 * arbitrary subset of four sections. Checked in the browser at 1440 with this band
 * off: the seam is clean.
 *
 * `STACK_BAND` → `false` removes the `<StackBand />` section the same way: the
 * heading, the technology wordmark grid, the mechanism lines and the "where each one
 * lives" disclosure. It reads no endpoint, so nothing else is affected.
 *
 * The switches are independent. Turning both off returns the page to exactly the six
 * sections it had before either band existed.
 *
 * ## How to flip one
 *
 * Two ways, and the constant below is the authority for both.
 *
 * 1. **Edit this file** — change the default in {@link bandEnabled}'s call below to
 *    `false`. One line, one file, no other edit anywhere.
 * 2. **Set an environment variable**, for turning it off without a code change:
 *
 *    ```
 *    NEXT_PUBLIC_STANDARDS_BAND=off
 *    NEXT_PUBLIC_STACK_BAND=off
 *    ```
 *
 *    `off`, `false`, `0` and `no` all mean off, case-insensitively. Next.js inlines
 *    `NEXT_PUBLIC_*` at build time, so a running dev server needs a restart and a
 *    deployment needs a rebuild — which is why the constant, not the variable, is the
 *    primary control.
 *
 * `web/tests/landing/bandsSwitch.test.mjs` asserts both directions: that the page
 * renders each band only through its switch, and that no other module reaches around
 * it to import the band's parts directly.
 */

/** Values that mean "off", case-insensitively. Everything else leaves the band on. */
const OFF = new Set(['off', 'false', '0', 'no'])

/**
 * Resolve one band's switch: the env override if it is set, otherwise the default.
 *
 * Exported so a test can exercise both directions without mutating `process.env` and
 * without depending on how Next.js inlines a public variable.
 *
 * @param override - The raw environment value, or `undefined` when it is unset.
 * @param fallback - What the band does when nothing overrides it.
 * @returns Whether the band renders.
 */
export function bandEnabled(override: string | undefined, fallback: boolean): boolean {
  if (override === undefined || override.trim() === '') return fallback
  return !OFF.has(override.trim().toLowerCase())
}

/**
 * Whether the standards & compliance band renders. **This is the switch.**
 *
 * Set the second argument to `false` to remove the band described at the top of this
 * file. Nothing else needs editing.
 */
export const STANDARDS_BAND: boolean = bandEnabled(process.env.NEXT_PUBLIC_STANDARDS_BAND, true)

/**
 * Whether the technology band renders.
 *
 * Same contract, separate decision: the standards band is the one carrying framework
 * names, so the two are not tied together.
 */
export const STACK_BAND: boolean = bandEnabled(process.env.NEXT_PUBLIC_STACK_BAND, true)
