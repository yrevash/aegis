/**
 * When a top-bar disclosure closes — the two predicates, with no React in them.
 *
 * The text-size popover was keyboard-only for one reason, and it is worth writing down
 * because the same shape will be reached for again. Its rows are `sr-only`
 * `<input type="radio">` inside `<label>`s, and a `<label>` is not focusable. So a mouse
 * press on a row moved focus off the trigger and onto *nothing*: `blur` fired with
 * `relatedTarget === null`, the wrapper read "focus left me", the panel unmounted on
 * **mousedown**, and the `click` that would have checked the radio landed on a popover
 * that no longer existed. The bell's popover survived the identical gesture only because
 * its rows happen to be anchors, which take focus.
 *
 * `relatedTarget === null` does not mean "focus left this component". It means the
 * browser could not name where focus went — which is exactly what a press on a
 * non-focusable label, on padding, or on the panel's own background looks like. Closing
 * on it is closing on a press *inside* the menu.
 *
 * Dismissal is therefore split in two, and neither half guesses:
 *
 * - **Pointer** — a press whose target is outside the wrapper closes it. This is the only
 *   thing that dismisses a mouse or a touch, and it reads the press's own target rather
 *   than inferring it from focus.
 * - **Focus** — a blur that names an element outside the wrapper closes it. This is the
 *   keyboard's dismissal: `Tab` out and carry on down the page. A blur that names
 *   nothing closes nothing.
 *
 * Both take their `contains` as an argument, so `tests/settings/menuDismiss.test.mjs`
 * drives them under Node with no DOM at all.
 */

/**
 * Whether a `blur` out of the disclosure's wrapper should close it.
 *
 * @param open - Whether the panel is currently open.
 * @param relatedTarget - The node focus is moving **to**, or `null` when the browser
 *   cannot name one — a press on a label, on padding, or on the panel background.
 * @param contains - Whether the wrapper contains a given node.
 * @returns `true` only when focus has demonstrably landed somewhere else.
 */
export function shouldCloseOnBlur(
  open: boolean,
  relatedTarget: Node | null,
  contains: (node: Node) => boolean,
): boolean {
  if (!open) return false
  // The whole defect, in one line: focus going nowhere is not focus leaving.
  if (relatedTarget === null) return false
  return !contains(relatedTarget)
}

/**
 * Whether a pointer press should close the disclosure.
 *
 * @param open - Whether the panel is currently open.
 * @param target - The pressed node, or `null` when the event carries none.
 * @param contains - Whether the wrapper contains a given node.
 * @returns `true` only for a press that landed outside the wrapper.
 */
export function shouldCloseOnPointerDown(
  open: boolean,
  target: Node | null,
  contains: (node: Node) => boolean,
): boolean {
  if (!open) return false
  return target === null || !contains(target)
}
