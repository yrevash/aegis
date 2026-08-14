"""Typed media payloads — the one shape every guardrail entry point accepts.

**Why this module exists.** Before it, every guardrail entry point was ``str``
typed (``Rail = Callable[[str], ...]``) and the agent guarded ``state["query"]``,
a string. An image sent as an OpenAI multimodal content block was forwarded to
the model verbatim, having passed through *no rail at all* — and text rendered
inside an image is the standard prompt-injection vector against vision models.
A rail cannot screen what it cannot receive, so the contract had to widen from
"a string" to "a payload that knows what it is, how big it is, and where it came
from".

Three properties are load-bearing for security, so they are fields rather than
folklore:

* **bytes-or-URI** — a payload the process cannot read (a remote ``image_url``)
  is a payload the rails cannot screen. Making that distinction explicit lets
  the hygiene rail fail *closed* on it instead of waving it through.
* **declared MIME type** — always attacker-controlled. It is kept as
  ``mime_type`` and never trusted; :func:`aegis.media.sniff.sniff_mime` derives
  the real one from magic bytes and the hygiene rail compares the two.
* **provenance** — an image pulled out of a retrieved document or a tool result
  is the indirect-injection case (OWASP LLM01); one a human just uploaded is
  not. Rails and audits need to tell them apart.

Pydantic + stdlib only. No Pillow, no numpy, no codecs — so importing
:mod:`aegis.media` stays as cheap as importing :mod:`aegis.core`.
"""

from __future__ import annotations

import base64
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)


class MediaKind(StrEnum):
    """What a payload *is* — the discriminator of the payload union."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class MediaSource(StrEnum):
    """Where a payload came from — the trust dimension of provenance.

    The distinction is not cosmetic: ``RETRIEVAL`` and ``TOOL_OUTPUT`` are the
    *indirect* prompt-injection surfaces (OWASP LLM01) — content the user never
    typed, arriving from a document or an API the attacker may control. They are
    treated as untrusted by :attr:`Provenance.untrusted`, as is ``UNKNOWN``:
    a payload whose origin nobody recorded gets the strict treatment, never the
    lenient one.
    """

    USER_UPLOAD = "user_upload"
    TOOL_OUTPUT = "tool_output"
    RETRIEVAL = "retrieval"
    MODEL_OUTPUT = "model_output"
    UNKNOWN = "unknown"


class Provenance(BaseModel):
    """Where a payload came from, carried with the payload itself.

    Attributes:
        source: The coarse trust class (see :class:`MediaSource`).
        origin: Free-text detail for the audit trail — a filename, a URL, the
            name of the tool that produced it. Never parsed for control flow;
            it exists so a human reading a blocked verdict knows what was blocked.
    """

    model_config = ConfigDict(frozen=True)

    source: MediaSource = MediaSource.UNKNOWN
    origin: str | None = None

    @property
    def untrusted(self) -> bool:
        """Whether this payload must be treated as attacker-controlled content."""
        return self.source in {
            MediaSource.RETRIEVAL,
            MediaSource.TOOL_OUTPUT,
            MediaSource.UNKNOWN,
        }


class _BasePayload(BaseModel):
    """Fields shared by every payload kind (never instantiated directly).

    Frozen: a payload is evidence. A rail that wants to change one (the image-PII
    rail returns an actually-redacted image) returns a *new* payload, so the
    original bytes that were screened are still exactly what was screened.
    """

    model_config = ConfigDict(frozen=True)

    data: bytes | None = Field(
        default=None,
        description="The payload bytes, when the process holds them. Mutually exclusive "
        "with `uri`. JSON-serialised as base64 so a payload round-trips over the wire.",
    )
    uri: str | None = Field(
        default=None,
        description="A reference to bytes this process does NOT hold (e.g. a remote "
        "image_url). Rails cannot inspect these, so hygiene fails closed on them.",
    )
    mime_type: str = Field(
        description="The DECLARED content type. Attacker-controlled and never trusted; "
        "compare against aegis.media.sniff.sniff_mime() before believing it.",
    )
    declared_byte_size: int | None = Field(
        default=None,
        description="Byte size as claimed by the transport, for URI payloads whose bytes "
        "are not held. Ignored when `data` is present (bytes cannot lie about their length).",
    )
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("data", mode="before")
    @classmethod
    def _accept_base64(cls, value: Any) -> Any:  # noqa: ANN401 - pre-validation hook
        """Accept a base64 ``str`` for ``data`` so JSON payloads round-trip.

        Pydantic's default ``bytes`` coercion would UTF-8-encode the string, which
        corrupts binary content silently — exactly the class of bug this module
        exists to prevent.
        """
        if isinstance(value, str):
            return base64.b64decode(value, validate=True)
        return value

    @field_serializer("data", when_used="json")
    def _dump_base64(self, value: bytes | None) -> str | None:
        """Serialise ``data`` as base64 (binary is not valid UTF-8)."""
        return base64.b64encode(value).decode("ascii") if value is not None else None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> Self:
        """Require exactly one of ``data``/``uri``: ambiguity here is a security hole."""
        if (self.data is None) == (self.uri is None):
            raise ValueError("a media payload needs exactly one of `data` or `uri`")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def byte_size(self) -> int | None:
        """The payload's size in bytes — measured when held, declared when not.

        ``None`` means "unknown": a URI payload whose transport declared nothing.
        Callers must treat ``None`` as *unbounded*, never as zero.
        """
        if self.data is not None:
            return len(self.data)
        return self.declared_byte_size

    @property
    def inline(self) -> bool:
        """Whether the bytes are in hand (and therefore screenable)."""
        return self.data is not None

    def describe(self) -> str:
        """A short, PII-free identifier for logs and verdict text.

        Never includes the bytes or the decoded text — a verdict string travels
        into traces and the UI, and the whole point of the payload is that its
        content is untrusted.
        """
        size = "unknown size" if self.byte_size is None else f"{self.byte_size} bytes"
        where = "inline" if self.inline else f"uri={self.uri!r}"
        return f"<{self.kind.value} {self.mime_type} {size} {where}>"  # type: ignore[attr-defined]


class TextPayload(_BasePayload):
    """UTF-8 text — the payload the pre-existing string rails have always seen.

    Constructed automatically from a ``str`` at every widened entry point, so the
    legacy text path behaves byte-for-byte as it did before the media seam landed.
    """

    kind: Literal[MediaKind.TEXT] = MediaKind.TEXT
    mime_type: str = "text/plain"

    @classmethod
    def of(cls, text: str, *, provenance: Provenance | None = None) -> TextPayload:
        """Wrap a plain ``str`` as a :class:`TextPayload`.

        Args:
            text: The text to carry.
            provenance: Optional origin metadata.

        Returns:
            The payload, with ``data`` holding the UTF-8 encoding of ``text``.
        """
        # ``surrogatepass`` (here and in :attr:`text`) makes the round-trip lossless
        # for lone surrogates. Anything else would either raise or silently swap in
        # U+FFFD — and a guardrail that screens a *different* string from the one it
        # was handed is a guardrail with a bypass in it.
        return cls(
            data=text.encode("utf-8", errors="surrogatepass"),
            provenance=provenance or Provenance(),
        )

    @property
    def text(self) -> str:
        """The decoded text (lossless round-trip of :meth:`of`).

        Raises:
            ValueError: If the payload holds no bytes (a URI-only text payload
                cannot be decoded without fetching it — and this module never
                fetches anything).
        """
        if self.data is None:
            raise ValueError("text payload has no inline bytes to decode")
        return self.data.decode("utf-8", errors="surrogatepass")


class ImagePayload(_BasePayload):
    """An image — the payload class that motivated this whole module."""

    kind: Literal[MediaKind.IMAGE] = MediaKind.IMAGE
    mime_type: str = "image/png"


class AudioPayload(_BasePayload):
    """Audio, which is guarded by transcribing it first (see aegis.guardrails.media)."""

    kind: Literal[MediaKind.AUDIO] = MediaKind.AUDIO
    mime_type: str = "audio/wav"


#: The discriminated union every widened guardrail entry point accepts.
MediaPayload = Annotated[
    TextPayload | ImagePayload | AudioPayload,
    Field(discriminator="kind"),
]

#: Parses a serialised payload (dict/JSON) back into the right concrete class.
MEDIA_PAYLOAD_ADAPTER: TypeAdapter[MediaPayload] = TypeAdapter(MediaPayload)


def as_payload(value: str | TextPayload | ImagePayload | AudioPayload) -> MediaPayload:
    """Coerce ``value`` to a :class:`MediaPayload`, wrapping a bare ``str``.

    The compatibility hinge of the widened rail contract: every public guardrail
    entry point still takes ``str`` and routes it through here, so a text caller
    sees no behaviour change at all.

    Args:
        value: A payload, or a plain string to wrap as a :class:`TextPayload`.

    Returns:
        The payload.

    Raises:
        TypeError: If ``value`` is neither a string nor a payload.
    """
    if isinstance(value, str):
        return TextPayload.of(value)
    if isinstance(value, TextPayload | ImagePayload | AudioPayload):
        return value
    raise TypeError(f"expected str or MediaPayload, got {type(value).__name__}")


def payload_from_context(raw: object) -> MediaPayload | None:
    """Rebuild a payload from a serialised dict (the Colang/context wire form).

    Returns ``None`` for ``None``/empty input so a policy flow can treat "no media
    on this turn" as a no-op rather than an error.

    Args:
        raw: A ``dict`` produced by ``payload.model_dump(mode="json")``, or None.

    Returns:
        The payload, or ``None`` when there was nothing to rebuild.

    Raises:
        ValueError: If ``raw`` is a dict that does not validate as a payload —
            a malformed payload is an error, never a silent pass.
    """
    if raw is None or raw == "" or raw == {}:
        return None
    if isinstance(raw, TextPayload | ImagePayload | AudioPayload):
        return raw
    return MEDIA_PAYLOAD_ADAPTER.validate_python(raw)
