"""Every path that drains consolidation passes the rail. All of them, not most.

This is the fourth declared-but-unbound seam in this codebase — after `read_back_for`,
the first memory `screen`, and `max_tool_result_tokens` — and the memory one shipped
TWICE. The first fix bound the screen on the 60-second backstop sweeper, under a comment
reading "a screen the production path does not pass is not a guardrail, it is a
guardrail-shaped hole". The production path still did not pass it.

Worse, the unscreened path wins the race every time. Measured on a live deployment: every
consolidation job drained in 20-160 MILLISECONDS with `attempts=1`, while the screened
sweeper runs on a 60-second timer and can never claim a job already marked DONE. So the
screened caller existed, was correct, and never ran.

The proof it never fired was one query — `select op, count(*) from memory_write_log`
returned `ADD | 28` and no REFUSED row had ever been written.

So this test does not check that *a* caller passes the screen. It checks that **every**
caller does, by reading the source, because that is the property that was false while a
narrower test would have passed.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "app"


def _callers_of_sweep_pending() -> list[tuple[str, str]]:
    """Return (file, call-text) for every `sweep_pending(...)` call under app/."""
    out: list[tuple[str, str]] = []
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"await sweep_pending\((.*?)\n\s*\)", text, re.S):
            out.append((str(path.relative_to(_SRC)), m.group(1)))
    return out


def test_every_caller_of_sweep_pending_passes_the_screen() -> None:
    """The anti-vacuity guard is the count: there must be more than one caller."""
    callers = _callers_of_sweep_pending()
    assert len(callers) >= 2, (
        f"expected at least two drain paths, found {len(callers)} — if a caller was "
        "removed this test has stopped protecting what it was written for"
    )

    unscreened = [f for f, call in callers if "screen=" not in call]
    assert not unscreened, (
        f"these paths drain consolidation with no memory-write rail: {unscreened}. "
        "A screen one caller passes and another does not is not a guardrail; the "
        "unscreened path is simply the one that runs."
    )


def test_the_screen_lives_where_neither_drain_owns_it() -> None:
    """Structural, and the reason the defect recurred.

    While the screen was a private helper inside `app.main`, it belonged to the sweeper,
    and the agent-loop drain had no obvious reason to reach for it. Both now import the
    same function from a module neither owns, so there is no longer "the other one's"
    screen to forget.
    """
    from app.memory.screen import memory_write_screen

    assert callable(memory_write_screen)

    main = (_SRC / "main.py").read_text(encoding="utf-8")
    deps = (_SRC / "agent" / "deps.py").read_text(encoding="utf-8")
    for name, text in (("main.py", main), ("agent/deps.py", deps)):
        assert "from app.memory.screen import memory_write_screen" in text, (
            f"{name} does not import the shared screen — it has its own, which is how "
            "the two drain paths drifted apart the first time"
        )
