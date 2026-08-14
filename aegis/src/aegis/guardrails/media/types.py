"""The media verdict type — a GuardResult that can carry an image and its receipts.

Two things the text-only :class:`~aegis.core.types.GuardResult` cannot express,
both of which the audit that prompted this work called out:

* **A redacted artefact.** ``GuardResult.text`` is a string. When the image-PII
  rail paints over a passport number it must hand back *pixels*, so
  :attr:`MediaGuardResult.media` carries the replacement payload.
* **Which rails actually ran.** The previous audit found ``completer=None``
  silently disabling two rails while the verdict text still claimed all four had
  run. :attr:`rails_run` and :attr:`rails_skipped` make that structurally
  impossible to repeat: the reason line is *generated from* them, so a rail that
  did not run cannot appear in the sentence, and one that was skipped is named
  along with why.

Subclassing rather than widening ``GuardResult`` keeps the existing wire shape
byte-identical for every text caller that exists today.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from aegis.core.types import GuardResult
from aegis.media import MediaPayload


class MediaGuardResult(GuardResult):
    """A guard verdict about a non-text payload."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    media: MediaPayload | None = Field(
        default=None,
        description="The payload the caller should forward instead of the original — set "
        "when a rail rewrote it (e.g. an image with PII painted out). None means "
        "'forward the original'.",
    )
    rails_run: list[str] = Field(
        default_factory=list,
        description="Rails that actually executed, in order. The verdict reason is built "
        "from this list, so it can never overstate the coverage.",
    )
    rails_skipped: list[str] = Field(
        default_factory=list,
        description="Rails that did NOT execute, each with the reason it did not. A control "
        "that cannot run must fail closed AND say so.",
    )

    def coverage(self) -> str:
        """A sentence naming exactly which rails ran and which did not."""
        ran = ", ".join(self.rails_run) if self.rails_run else "none"
        parts = [f"Rails run: {ran}."]
        if self.rails_skipped:
            parts.append("Not run: " + "; ".join(self.rails_skipped) + ".")
        return " ".join(parts)
