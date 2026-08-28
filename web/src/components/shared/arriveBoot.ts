/**
 * The one line that stops the M1 entrance from flashing.
 *
 * `RevealGroup` hides its children before revealing them. Setting that hidden state from
 * JavaScript — even in a layout effect — cannot beat the first paint of server-rendered
 * HTML, so the sequence a reader actually sees is: content, blank, content again.
 * Measured on the landing hero before this existed: **284 ms** of flash at full speed and
 * **1705 ms** under 6× CPU throttle, which is not a subtle artefact, it is the page
 * visibly breaking on the slowest machines in the room.
 *
 * This is the same problem, and the same fix, as `TEXT_SCALE_BOOT` in
 * `components/settings/textScale.ts` — whose docstring already says it plainly: *"A
 * `useEffect` cannot do this — it runs after the first paint, which is precisely the
 * flash."*
 *
 * So the hidden state moves into CSS, and this script — synchronous, in `<head>`, before
 * any paint — marks the document as one where JavaScript is running. The CSS rule is
 * `.js [data-arrive] > *`, which means:
 *
 * - **With JS**, the class lands before first paint, children start hidden, and GSAP
 *   reveals them. No flash, because there was never a visible state to flash from.
 * - **Without JS**, the class never lands, the rule never matches, and every child
 *   renders normally. An entrance animation must never be able to blank a page for
 *   somebody whose JavaScript failed to load.
 *
 * It is deliberately not folded into `TEXT_SCALE_BOOT`: that module owns one concern and
 * is tested against its own string.
 */

/** Marks the document as JS-capable, synchronously, before the first paint. */
export const ARRIVE_BOOT = `(function(){try{document.documentElement.classList.add('js')}catch(e){}})();`
