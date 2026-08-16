"""The TypeScript stream-name mirror must match the Python frozenset exactly.

``web/src/lib/streamNames.ts`` hand-maintains a copy of
``aegis.core.stream_names.ALL`` so the console can recognise AG-UI ``CustomEvent``
names. A hand-maintained mirror drifts unless something compares the two, and the
thing that claimed to do the comparing was::

    export const STREAM_NAME_COUNT = STREAM_NAME_SET.size

with a header comment promising "parity is asserted by the count below". That count is
derived from the TypeScript list, so it only ever compared the list to itself. It could
not fail, and while it sat there the mirror silently lost five names — every event added
by the media, voice and vision modules.

This test is the real comparison. It lives backend-side rather than in ``aegis/`` because
the importable core must not know a web console exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from aegis.core.stream_names import ALL

#: Repo root, from ``backend/tests/api/`` → ``backend/`` → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIRROR = _REPO_ROOT / "web" / "src" / "lib" / "streamNames.ts"

#: Matches the string literal of each ``KEY: 'value',`` entry in ``STREAM_NAMES``.
_ENTRY = re.compile(r"^\s*[A-Z0-9_]+:\s*'([a-z0-9_]+)'", re.MULTILINE)


def _mirror_names() -> set[str]:
    """Parse the declared event names out of the TypeScript mirror."""
    block = _MIRROR.read_text(encoding="utf-8")
    start = block.index("export const STREAM_NAMES")
    end = block.index("} as const", start)
    return set(_ENTRY.findall(block[start:end]))


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_typescript_mirror_matches_python_stream_names() -> None:
    """Every Python stream name appears in the mirror, and nothing extra does."""
    mirror = _mirror_names()

    missing = sorted(ALL - mirror)
    extra = sorted(mirror - ALL)

    assert not missing, (
        f"web/src/lib/streamNames.ts is missing {missing}. The backend emits these "
        f"CustomEvent names but the console does not recognise them."
    )
    assert not extra, (
        f"web/src/lib/streamNames.ts declares {extra}, which aegis.core.stream_names.ALL "
        f"does not. Either the name was removed backend-side or the mirror invented one."
    )


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_the_parser_actually_finds_names() -> None:
    """Guard the guard: an empty parse would make the test above vacuously pass.

    This is the failure mode the mirror's own count check had — a check whose
    subject can silently become empty proves nothing when it passes.
    """
    assert len(_mirror_names()) >= 20
