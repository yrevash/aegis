# Skill: The urgent path

Use when the request is time-critical and the caller has said so explicitly.

- Confirm the specific record (its id, its current state, when it was raised) before
  acting on it; never guess which one is meant.
- Say plainly what the fast path costs and what it skips, so the caller is choosing it
  rather than discovering it.
- A state change is a write action — propose it through the approval gate, do not assert
  it as already done.
- Close by naming the next checkpoint and who owns it.
