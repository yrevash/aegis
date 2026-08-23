/**
 * The classes that stop a hairline grid ending in a tinted hole.
 *
 * These grids are drawn the way the rest of the landing page draws them: a `bg-border`
 * container with `gap-px` between white cells, so the one-pixel gaps *are* the rules.
 * The side effect is that any cell position the list does not fill shows the container
 * through it — a tinted rectangle at the end of the grid that reads as a card which
 * failed to render. `LivePlatform` hit this first, with an odd module manifest, and
 * fixed it in place for one grid at one breakpoint.
 *
 * This is the same fix generalised, because both new bands have the problem and at two
 * breakpoints each: thirteen technology claims leave one orphan in a three-column grid
 * and one in a two-column grid, and the three India frameworks leave one in the
 * two-column grid. The last cell simply widens to fill its row.
 *
 * The class strings are written out as literals rather than composed, because Tailwind
 * scans source text: a template literal building `sm:col-span-${n}` produces a class
 * that exists in the DOM and in no stylesheet.
 */

/**
 * The `col-span` classes the **last** cell of a grid needs, given how many cells it has.
 *
 * Assumes the grid this page uses everywhere: one column below `sm`, two at `sm`, three
 * at `lg`.
 *
 * **The `lg` span is always emitted, including `lg:col-span-1`,** and that is the whole
 * subtlety. A breakpoint class keeps applying at every width above it, so three cells —
 * which need `sm:col-span-2` to fill the two-column grid — carried that span up into the
 * three-column grid, where the last cell then took two of three columns, wrapped onto
 * its own row, and left an L-shaped tinted hole *larger* than the one being fixed. It
 * shipped that way for one screenshot. `lg` has to say what it wants rather than
 * inherit it.
 *
 * @param count - How many cells the grid holds.
 * @returns A class string for the last cell.
 */
export function gridTail(count: number): string {
  const sm = count % 2 === 1 ? 'sm:col-span-2' : 'sm:col-span-1'
  const lg =
    count % 3 === 1 ? 'lg:col-span-3' : count % 3 === 2 ? 'lg:col-span-2' : 'lg:col-span-1'
  return `${sm} ${lg}`
}
