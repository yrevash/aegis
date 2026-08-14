"""Guard tests: aegis.voice is importable and isolated (the Module Contract).

Two properties, both checked in a subprocess so another test's ``sys.modules``
cannot hide a real import:

1. Importing ``aegis.voice`` pulls **no** heavy dependency — not even ``litellm``,
   which the gateway it drives imports lazily. A light API schema layer must be
   able to import the voice types without the model stack.
2. ``aegis.voice`` imports nothing from the host application (``app.*``). It is a
   library, not a layer of this backend.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _run(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a subprocess with ``aegis`` resolved from the source tree."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": _SRC},
    )


def test_importing_voice_pulls_no_heavy_deps() -> None:
    """Importing aegis.voice must not drag in litellm, torch, langgraph, xgboost or fastapi."""
    code = (
        "import sys; import aegis.voice; "
        "banned = {'litellm','torch','langgraph','xgboost','fastapi','numpy','pandas'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_voice_imports_nothing_from_the_host_app() -> None:
    """No ``app.*`` module may appear after importing aegis.voice."""
    code = (
        "import sys; import aegis.voice; "
        "hit = [m for m in sys.modules if m == 'app' or m.startswith('app.')]; "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_voice_is_usable_with_only_the_base_install() -> None:
    """The whole guarded path runs with pydantic alone — fakes stand in for the fleet."""
    code = (
        "import asyncio, io, wave, array, math\n"
        "from aegis.media import AudioPayload\n"
        "from aegis.voice import transcribe_and_guard\n"
        "from aegis.core.types import GuardResult, GuardVerdict\n"
        "from aegis.gateway.types import TranscriptionResult, Usage\n"
        "buf = io.BytesIO()\n"
        "w = wave.open(buf, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)\n"
        "w.writeframes(array.array('h', [100] * 8000).tobytes()); w.close()\n"
        "async def fake(audio, **kw):\n"
        "    return TranscriptionResult(text='hi', usage=Usage())\n"
        "async def rails(text):\n"
        "    return GuardResult(verdict=GuardVerdict.PASS, reason='ok', text=text)\n"
        "p = AudioPayload(data=buf.getvalue(), mime_type='audio/wav')\n"
        "r = asyncio.run(transcribe_and_guard(p, text_check=rails, transcriber=fake))\n"
        "assert r.agent_input == 'hi', r\n"
    )
    proc = _run(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr
