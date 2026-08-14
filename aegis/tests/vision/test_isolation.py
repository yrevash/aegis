"""Importing aegis.vision pulls only core/media/guardrails — no gateway, no torch.

The Module Contract's first pillar. ``aegis.vision`` decides ordering and owns no
provider, so importing it must not drag in litellm, the agent graph, the ML
stack, or a web framework — and, given this is the *vision* module, it must
especially not pull a local vision model. Fleet-only is a policy, and a policy
that is not tested is folklore.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")

#: Banned at import time. ``torch``/``transformers``/``timm`` are the local-vision
#: stack this platform is not allowed to use; the rest are the platform megadeps.
_BANNED = (
    "'litellm', 'torch', 'transformers', 'timm', 'langgraph', 'xgboost', "
    "'fastapi', 'sqlalchemy', 'nemoguardrails', 'presidio_image_redactor'"
)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a clean subprocess with the source tree on the path."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )


def test_importing_vision_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.vision does not pull the platform or a local model."""
    code = (
        "import sys; "
        "import aegis.vision; "
        "import aegis.vision.pipeline; "
        "import aegis.vision.pii; "
        "import aegis.vision.stream; "
        f"banned = {{{_BANNED}}}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_vision_imports_no_app_module() -> None:
    """Verify aegis.vision never reaches back into the host application package."""
    code = (
        "import sys; "
        "import aegis.vision; "
        "hit = [m for m in sys.modules if m == 'app' or m.startswith('app.')]; "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pillow_is_only_imported_when_the_pii_rail_runs() -> None:
    """PIL is a lazy, opt-in dependency — importing the module must not need it."""
    code = (
        "import sys; "
        "import aegis.vision; "
        "hit = [m for m in sys.modules if m == 'PIL' or m.startswith('PIL.')]; "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
