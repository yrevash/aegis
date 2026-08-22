'use client'

import { useCallback, useEffect, useRef, type ReactElement, type ReactNode } from 'react'

/**
 * Everything inside the card that can take focus, in document order.
 *
 * Module-level so the trap does not rebuild the selector on every keystroke. `[hidden]`
 * and `disabled` are excluded because a resolved gate disables both of its buttons, and
 * a trap whose only two stops are unfocusable would send Tab straight back out.
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/** The focusable descendants of `root`, minus anything hidden from layout. */
function focusablesIn(root: HTMLElement | null): HTMLElement[] {
  if (root === null) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  )
}

/**
 * The human-approval spotlight. When the run pauses at the gate, this scrims and blurs
 * the whole console and lifts the {@link ApprovalCard} forward — the one moment the
 * demo asks a human to decide becomes the only thing on screen.
 *
 * ## The focus trap, which used to be a comment rather than a behaviour
 *
 * This component declared `role="dialog" aria-modal="true"` and claimed in its own
 * docstring to trap focus "by covering the dimmed surface behind it". Covering a surface
 * is a *visual* fact; it does nothing to the tab order. A keyboard user landed on this
 * screen with the whole console still tabbable underneath a blur they could not see
 * through, and `aria-modal="true"` told a screen reader that everything outside was
 * inert while it was not. That is the single worst place in this product to be wrong
 * about, because it is the one moment a human is being asked to approve a high-risk
 * action.
 *
 * So the trap is real now, and it is four separate behaviours:
 *
 * - **The card takes focus on open**, not the first button. Focusing Approve would put a
 *   destructive decision one Space away from a stray keystroke; focusing the card means
 *   the decision is read before it can be made, and Tab reaches the buttons in one step.
 * - **Tab and Shift+Tab wrap inside the card.** Handled on `keydown` in the capture
 *   phase so it fires before anything the card itself listens for.
 * - **`focusin` pulls focus back** if it lands outside anyway — a click on the scrim, a
 *   programmatic `focus()` elsewhere, or a browser that restores focus after a repaint.
 * - **Focus returns to whatever had it** when the gate closes, so resolving an approval
 *   does not drop the person at the top of the document.
 *
 * `onClose` is optional and unwired today, deliberately: a decision gate with a dismiss
 * is a gate you can walk past. Escape calls it when a caller supplies one, and is a
 * no-op otherwise rather than pretending to close something it cannot.
 */
export function ApprovalSpotlight({
  children,
  onClose,
}: {
  children: ReactNode
  /**
   * Dismiss the gate on Escape. Omitted by the console today — the run is paused on
   * this decision and there is nothing to return to until it is made.
   */
  onClose?: () => void
}): ReactElement {
  const cardRef = useRef<HTMLDivElement>(null)
  /** Whatever had focus when the gate opened, to hand it back on close. */
  const restoreRef = useRef<HTMLElement | null>(null)

  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  const focusCard = useCallback(() => {
    cardRef.current?.focus()
  }, [])

  useEffect(() => {
    const active = document.activeElement
    restoreRef.current = active instanceof HTMLElement ? active : null

    // The card, not its first button: see the docstring.
    focusCard()

    const onKeyDown = (event: KeyboardEvent): void => {
      const card = cardRef.current
      if (card === null) return

      if (event.key === 'Escape') {
        const close = onCloseRef.current
        if (close !== undefined) {
          event.preventDefault()
          event.stopPropagation()
          close()
        }
        return
      }

      if (event.key !== 'Tab') return

      const items = focusablesIn(card)
      if (items.length === 0) {
        event.preventDefault()
        focusCard()
        return
      }

      const first = items[0]
      const last = items[items.length - 1]
      const inside = card.contains(document.activeElement)

      if (event.shiftKey) {
        if (!inside || document.activeElement === first || document.activeElement === card) {
          event.preventDefault()
          last?.focus()
        }
      } else if (!inside || document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }

    // The belt to the keydown handler's braces: anything that moves focus without a Tab
    // still lands back on the decision.
    const onFocusIn = (event: FocusEvent): void => {
      const card = cardRef.current
      if (card === null) return
      if (event.target instanceof Node && card.contains(event.target)) return
      focusCard()
    }

    // A press on the scrim is the one case `focusin` cannot catch: focus goes to
    // `<body>`, which fires a blur and no focusin at all, so the trap was silently
    // leaking on a single click. Cancelling the press *before* it moves focus is the
    // fix — `preventDefault` on `mousedown` is what stops the default focus change,
    // and doing it here rather than on `focusout` means focus never leaves in the
    // first place instead of flickering out and back.
    const onMouseDown = (event: MouseEvent): void => {
      const card = cardRef.current
      if (card === null) return
      if (event.target instanceof Node && card.contains(event.target)) return
      event.preventDefault()
      focusCard()
    }

    document.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('focusin', onFocusIn, true)
    document.addEventListener('mousedown', onMouseDown, true)
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('focusin', onFocusIn, true)
      document.removeEventListener('mousedown', onMouseDown, true)
      const back = restoreRef.current
      if (back !== null && back.isConnected) back.focus()
    }
  }, [focusCard])

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Human approval required"
    >
      <div aria-hidden className="absolute inset-0 bg-foreground/30 backdrop-blur-[2px]" />
      <div
        ref={cardRef}
        tabIndex={-1}
        className="animate-trace-in relative z-10 w-full max-w-md min-w-0 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {children}
      </div>
    </div>
  )
}
