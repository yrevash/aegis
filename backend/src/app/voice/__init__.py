"""Backend wiring for ``aegis.voice`` — the host half of the speech-to-text path.

The speech-to-text module itself is standalone and host-agnostic (see
``/aegis/src/aegis/voice``). It takes its two dependencies by injection: the
gateway call that reaches the fleet's hosted Whisper deployment, and the text
rail stack that screens the transcript. This package is the **composition root**
that supplies this platform's versions of both:

* the rails are :func:`app.guardrails.check_input` — the *same* function the agent
  graph guards typed input with, so a spoken turn and a typed turn are judged by
  one policy (including whichever engine, programmatic or NeMo Colang, the
  operator selected, and any custom rail they added);
* the gateway call is ``aegis.gateway.transcribe``, already wired to this app's
  settings, governance hook and OTel sink by the ``app.core.llm`` shim, so a
  transcription is budget-enforced and ledgered exactly like every other call.

Nothing about the security ordering lives here. Transcribe-then-guard, and the
fail-closed direction of every failure, are properties of ``aegis.voice``; this
package would have to go out of its way to break them, and the security tests in
both suites assert that it does not.
"""

from __future__ import annotations

from app.voice.service import (
    MAX_UPLOAD_BYTES,
    AudioTooLarge,
    read_upload,
    transcribe_upload,
)

__all__ = [
    "MAX_UPLOAD_BYTES",
    "AudioTooLarge",
    "read_upload",
    "transcribe_upload",
]
