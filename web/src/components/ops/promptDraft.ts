/**
 * The two rules the prompt editor cannot get wrong, as functions rather than as
 * conditions buried in a component.
 *
 * Both exist because of the same defect. `GET /llmops/prompts` answers
 * `activePrompt: null` for any scope with no promoted version — the prompt running there
 * is the shipped persona prompt, which is not on that wire — and the editor folded that
 * null into `''`. A box labelled "Task prompt" therefore rendered blank while a prompt
 * was live, and the operator's first edit was not an edit at all: it became the scope's
 * *entire* task prompt. A tenant's operating prompt was replaced by one sentence that
 * way.
 *
 * @see ./PromptControl.tsx
 */

/**
 * Whether the editor may (re)seed itself from the live prompt.
 *
 * The box is the operator's the moment they type in it, and it is the server's until
 * then. So: seed an empty box, seed a box still holding exactly what was last seeded
 * into it — which is what lets an activate or a rollback move the text under an
 * untouched editor — and never seed over typing.
 *
 * @param current - What is in the box now.
 * @param seededFrom - What was last seeded into it, or `null` if nothing ever was.
 * @returns `true` when the box may be replaced by the live prompt.
 */
export function mayReseed(current: string, seededFrom: string | null): boolean {
  return current === '' || current === seededFrom
}

/**
 * Why a draft cannot be saved as a version, or `null` when it can.
 *
 * **A blank body is refused, not confirmed.** Every other irreversible control on this
 * panel asks first, because each has an answer a person genuinely means. This one does
 * not: an empty or whitespace-only task prompt expresses no instruction, and promoting
 * it leaves the tenant running on the platform floor alone. Asking someone to confirm an
 * outcome nobody can intend is not a safeguard, it is one more dialog to click through.
 *
 * It cannot be left to the server either: `PromptDraftRequest.system_prompt` is
 * `min_length=1`, so a single space is a valid version as far as the API is concerned.
 *
 * @param draft - The body in the box.
 * @returns The refusal to show, or `null` if the draft is saveable.
 */
export function draftRefusal(draft: string): string | null {
  if (draft.trim().length > 0) return null
  return 'A version needs a body. An empty prompt would leave only the platform floor.'
}
