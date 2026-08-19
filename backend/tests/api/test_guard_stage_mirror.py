"""The TypeScript ``GuardStage`` union must match the Python enum exactly.

``web/src/lib/stream.ts`` hand-maintains a string-literal union mirroring
``aegis.core.types.GuardStage``. Nothing compared the two, which is how the union sat
at ``'input' | 'output'`` while a third stage was being specified: a console that does
not know a stage exists renders a guardrail that fired as an unknown, or not at all —
and a rail nobody can see is the failure mode this whole stage was added to fix.

Sibling of ``test_stream_name_mirror.py``, and backend-side for the same reason: the
importable core must not know a web console exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from aegis.core.types import GuardStage

#: Repo root, from ``backend/tests/api/`` → ``backend/`` → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIRROR = _REPO_ROOT / "web" / "src" / "lib" / "stream.ts"

#: Matches each ``'literal'`` in the ``export type GuardStage = ...`` declaration.
_LITERAL = re.compile(r"'([a-z_]+)'")


def _mirror_stages() -> set[str]:
    """Parse the declared stage literals out of the TypeScript union."""
    text = _MIRROR.read_text(encoding="utf-8")
    start = text.index("export type GuardStage")
    end = text.index("\n", start)
    return set(_LITERAL.findall(text[start:end]))


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_typescript_mirror_matches_the_python_guard_stage_enum() -> None:
    """Every Python stage appears in the union, and the union invents nothing."""
    mirror = _mirror_stages()
    python = {stage.value for stage in GuardStage}

    missing = sorted(python - mirror)
    extra = sorted(mirror - python)

    assert not missing, (
        f"web/src/lib/stream.ts is missing GuardStage {missing}. The backend stamps "
        f"these on guardrail events and the console cannot type them."
    )
    assert not extra, (
        f"web/src/lib/stream.ts declares GuardStage {extra}, which aegis.core.types "
        f"does not. Either the stage was removed or the mirror invented one."
    )


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_the_parser_actually_finds_stages() -> None:
    """Guard the guard: an empty parse would make the test above vacuously pass."""
    assert len(_mirror_stages()) >= 3


def test_the_tool_result_stage_exists_at_all() -> None:
    """Phase-05 §5.7: tool output gets screened before it reaches any agent context."""
    assert GuardStage.TOOL_RESULT.value == "tool_result"
