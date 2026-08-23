/**
 * The pointer target under a control that is drawn smaller than a finger.
 *
 * WCAG 2.2 AA (2.5.8, Target Size (Minimum)) puts the floor at **24 × 24 CSS px**, and
 * the rest of this console clears it with room to spare — every `Button` is `size-11`,
 * 44px. The small icon-only affordances did not: the ⓘ trigger is a 1rem box, the
 * write-log disclosure chevron is 0.875rem, and a flex row squeezed some of them
 * narrower still (17.5px, 19.3px, 19.5px measured). Those are all under 24px at every
 * text size, and they are *worse* at the largest one, which is the setting chosen by
 * the people least able to hit a 17px box.
 *
 * The fix is not a bigger glyph. A 24px ⓘ beside a 13px label is a different design and
 * a noisier page; what the criterion asks for is a target that size, not a *drawing*
 * that size. So the control keeps its measurements and grows an invisible pointer
 * surface around them: an absolutely-positioned pseudo-element, which takes no space in
 * the flow, moves nothing beside it, and belongs to the button — so a click that lands
 * on it activates the button.
 *
 * `-inset-1` is 0.25rem of overhang per side: 16 + 8 = 24px at the default root size,
 * 20 + 10 = 30px at 125%, and it scales with the text setting because it is a rem. The
 * `shrink-0` is the other half of the defect — without it a flex row shrinks the box
 * below its own size, which is where the 17.5px and 19.3px measurements came from, and
 * the overhang cannot recover a target the layout is still squeezing.
 *
 * Not a substitute for sizing a control properly where a control can be sized properly.
 * This is for the deliberately small ones.
 */
export const TAP_TARGET =
  'relative shrink-0 before:absolute before:-inset-1 before:content-[""]'
