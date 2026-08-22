/**
 * The declare-a-peer drawer's submit lifecycle — kept pure so it can be asserted.
 *
 * The drawer used to close and blank itself the instant `onCreate` was *called*, before
 * the registry had answered. A refused declaration — `audit_peer` is not a usable tool
 * namespace, 400, a sentence the API takes the trouble to write — therefore cost the
 * operator the endpoint, the auth header and the credential they had just typed, and the
 * reason landed in a page-level banner above a drawer that was no longer open.
 *
 * A refusal is not an outcome the form should discard itself over. It keeps everything,
 * including the credential: the secret is in this component's state either way, so
 * blanking the input bought no safety and cost a retype on every typo.
 *
 * {@link beforeSubmit} exists for the second half of the same defect: a submit that never
 * reaches the server — an empty id — left the *previous* attempt's banner standing, so a
 * refused-then-blocked sequence read as though something had just succeeded. Every
 * attempt clears the last verdict first.
 */

/** The drawer's outcome banner. One at a time; always the current attempt's. */
export interface DeclareNotice {
  kind: 'ok' | 'error'
  text: string
}

/** Everything the drawer owns between one submit and the next. */
export interface DeclareState<TDraft> {
  /** Whether the drawer is open. A refusal must not change this. */
  open: boolean
  draft: TDraft
  notice: DeclareNotice | null
}

/** What came back: `null` reason means the registry accepted it. */
export interface DeclareResult {
  reason: string | null
}

/** Clear the last attempt's verdict. Called on *every* submit, valid or not. */
export function beforeSubmit<TDraft>(state: DeclareState<TDraft>): DeclareState<TDraft> {
  return state.notice === null ? state : { ...state, notice: null }
}

/** Refuse locally, without a request: the drawer stays open and keeps the draft. */
export function refuseLocally<TDraft>(
  state: DeclareState<TDraft>,
  text: string,
): DeclareState<TDraft> {
  return { ...state, open: true, notice: { kind: 'error', text } }
}

/**
 * Fold the registry's answer back into the drawer.
 *
 * @param state The state as of the attempt (already through {@link beforeSubmit}).
 * @param result The registry's verdict.
 * @param empty The blank draft to reset to — only ever used on acceptance.
 * @param accepted The sentence to show when it worked.
 */
export function afterSubmit<TDraft>(
  state: DeclareState<TDraft>,
  result: DeclareResult,
  empty: TDraft,
  accepted: string,
): DeclareState<TDraft> {
  if (result.reason !== null) {
    // Open, and the draft untouched — the operator's next act is one edit and one click.
    return { ...state, open: true, notice: { kind: 'error', text: result.reason } }
  }
  return { open: false, draft: empty, notice: { kind: 'ok', text: accepted } }
}
