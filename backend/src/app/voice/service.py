"""Read an uploaded recording, transcribe it, and screen the transcript.

Two jobs, in this order, and no others:

1. **Get the bytes safely.** A multipart upload is attacker-supplied and
   unbounded until proven otherwise, so :func:`read_upload` streams it with a
   hard byte cap and refuses the moment the cap is passed — rather than reading
   the whole thing into memory and *then* measuring it, which is the denial of
   service the cap exists to prevent.
2. **Hand it to ``aegis.voice`` with this platform's rails wired in.** The
   ordering, the chunking and every fail-closed direction belong to the library;
   this module only supplies the two injected dependencies.
"""

from __future__ import annotations

import logging

from aegis.media import AudioPayload, MediaLimits, MediaSource, Provenance
from aegis.voice import VoiceResult, transcribe_and_guard
from fastapi import UploadFile

from app.guardrails import check_input

logger = logging.getLogger(__name__)

#: Hard cap on one upload. Deliberately the same number as
#: :attr:`aegis.media.MediaLimits.max_bytes`, so the transport-level refusal and
#: the payload-hygiene refusal agree; a caller can never get past one and be
#: stopped by the other with a confusing message.
MAX_UPLOAD_BYTES: int = MediaLimits().max_bytes

#: Read granularity for the streaming size check (64 KiB).
_READ_CHUNK = 64 * 1024


class AudioTooLarge(ValueError):
    """The upload exceeded :data:`MAX_UPLOAD_BYTES` and was abandoned mid-read."""

    def __init__(self, cap: int) -> None:
        """Record the cap that was exceeded.

        Args:
            cap: The byte cap in force.
        """
        super().__init__(f"Audio upload exceeds the {cap}-byte cap.")
        self.cap = cap


async def read_upload(upload: UploadFile, *, cap: int | None = None) -> bytes:
    """Read ``upload`` into memory, refusing anything over ``cap``.

    Streamed rather than read whole: the point of a cap is to stop before the
    memory is spent, so the read is abandoned as soon as the running total passes
    it.

    Args:
        upload: The multipart file part.
        cap: Maximum bytes to accept; :data:`MAX_UPLOAD_BYTES` when omitted. Read
            at call time (not bound as a default) so an operator or a test can
            change the module-level cap and have it take effect.

    Returns:
        The uploaded bytes.

    Raises:
        AudioTooLarge: If the upload is larger than ``cap``.
    """
    cap = MAX_UPLOAD_BYTES if cap is None else cap
    buf = bytearray()
    while True:
        block = await upload.read(_READ_CHUNK)
        if not block:
            return bytes(buf)
        buf.extend(block)
        if len(buf) > cap:
            raise AudioTooLarge(cap)


async def transcribe_upload(
    data: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    language: str | None = None,
) -> VoiceResult:
    """Transcribe uploaded audio and screen the transcript with this app's rails.

    The declared ``content_type`` is carried onto the payload **as a declaration,
    not as a fact**: :mod:`aegis.media` sniffs the magic bytes and refuses the
    upload when the two disagree. That mismatch check is the whole reason the
    declared type is recorded rather than discarded.

    Args:
        data: The uploaded bytes.
        filename: Original filename, for the audit trail and the payload's
            provenance. Never parsed for control flow.
        content_type: The declared MIME type from the multipart part.
        language: Optional ISO-639-1 hint; ``None`` lets the model auto-detect.

    Returns:
        The :class:`~aegis.voice.types.VoiceResult` — transcript, verdict and the
        itemised list of which controls ran.
    """
    payload = AudioPayload(
        data=data,
        mime_type=(content_type or "application/octet-stream").split(";")[0].strip(),
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin=filename),
    )
    # `check_input` is the platform's entire input rail stack — the same one the
    # agent graph runs on typed input. Passing it here is what makes a spoken turn
    # and a typed turn subject to one policy rather than two that can drift.
    return await transcribe_and_guard(payload, text_check=check_input, language=language)
