"""Rail-contract adapter — how ``Rail`` widened from ``str`` without breaking anything.

The rail contract used to be ``Callable[[str], GuardResult | None]``. It is now
``Callable[[MediaPayload], GuardResult | None]``, because a rail that can only
receive a string can never screen an image, and an image nobody screens is an
unguarded path to the model.

Widening a public callback type is normally a breaking change. It is not one
here, and this module is the reason: :func:`call_rail` looks at what a rail was
written to accept and hands it exactly that.

* A **media rail** — one whose parameter is annotated with a payload type, or
  one wearing the :func:`media_rail` decorator — receives the payload.
* A **legacy text rail** — anything else, including an unannotated ``lambda`` —
  receives ``payload.text``, exactly the string it used to get. Byte-for-byte
  the old behaviour for the old callers.

**Deprecation path.** Legacy string rails are supported indefinitely for
:class:`aegis.media.TextPayload` and will keep working; what they cannot do is
judge an image or an audio clip. Rather than passing them a stringified blob
(meaningless) or crashing (hostile), :func:`call_rail` *skips* them for non-text
payloads and says so through ``on_skip`` — the pipeline records the skip in the
verdict so nothing ever claims a rail ran when it did not. New rails should take
a ``MediaPayload`` and use ``@media_rail`` when the annotation is not available
(``functools.partial``, a callable class, a C-level callable).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aegis.core.types import GuardResult
from aegis.media import AudioPayload, ImagePayload, MediaKind, MediaPayload, TextPayload

logger = logging.getLogger(__name__)

#: Marker attribute set by :func:`media_rail`.
MEDIA_RAIL_ATTR = "__aegis_media_rail__"

#: Annotation spellings that mean "this rail takes a payload". Compared as strings
#: because ``from __future__ import annotations`` makes every annotation a string
#: at runtime, and resolving them would import the caller's module namespace.
_PAYLOAD_ANNOTATIONS = frozenset(
    {"MediaPayload", "TextPayload", "ImagePayload", "AudioPayload"}
)

RailResult = GuardResult | None
#: A rail in the widened contract.
MediaRailFn = Callable[[MediaPayload], "RailResult | Awaitable[RailResult]"]


def media_rail(fn: MediaRailFn) -> MediaRailFn:
    """Mark ``fn`` as taking a :class:`~aegis.media.MediaPayload`, not a ``str``.

    Only needed when the annotation cannot be read (a ``functools.partial``, a
    callable object, a lambda). An annotated ``def rail(payload: MediaPayload)``
    is detected without it.

    Args:
        fn: The rail callable.

    Returns:
        ``fn``, marked.
    """
    setattr(fn, MEDIA_RAIL_ATTR, True)
    return fn


def _first_annotation(rail: object) -> str | None:
    """Return the first positional parameter's annotation, as a string, or ``None``."""
    target = rail if inspect.isfunction(rail) or inspect.ismethod(rail) else None
    if target is None:
        target = getattr(rail, "__call__", None)  # noqa: B004 - a callable object's method
        if target is None:
            return None
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):  # builtins / C callables have no signature
        return None
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }:
            annotation = parameter.annotation
            if annotation is inspect.Parameter.empty:
                return None
            if isinstance(annotation, str):
                return annotation
            return getattr(annotation, "__name__", str(annotation))
    return None


def is_media_rail(rail: object) -> bool:
    """Whether ``rail`` accepts a payload (vs. the legacy ``str``).

    Args:
        rail: The rail callable.

    Returns:
        ``True`` when the rail is marked with :func:`media_rail` or annotates its
        first parameter with a payload type; ``False`` (legacy text rail) otherwise.
    """
    if getattr(rail, MEDIA_RAIL_ATTR, False):
        return True
    annotation = _first_annotation(rail)
    if annotation is None:
        return False
    # Tolerates unions and qualified spellings: "MediaPayload | None",
    # "aegis.media.ImagePayload", "ImagePayload".
    return any(name in annotation for name in _PAYLOAD_ANNOTATIONS)


def call_rail(
    rail: Callable[..., Any],
    payload: MediaPayload,
    *,
    on_skip: Callable[[str], None] | None = None,
) -> RailResult | Awaitable[RailResult]:
    """Invoke ``rail`` with whatever it was written to accept.

    Args:
        rail: A media rail or a legacy string rail.
        payload: The payload under test.
        on_skip: Called with a human-readable reason when a legacy string rail
            cannot judge this payload kind. The pipeline uses it to record the
            skip in the verdict — a rail that did not run must never be counted
            among the rails that did.

    Returns:
        The rail's result (possibly awaitable), or ``None`` when the rail was
        skipped as inapplicable.
    """
    if is_media_rail(rail):
        return rail(payload)
    if isinstance(payload, TextPayload):
        return rail(payload.text)
    name = getattr(rail, "__name__", rail.__class__.__name__)
    reason = (
        f"custom text rail {name!r} skipped: it takes a str and cannot judge a "
        f"{payload.kind.value} payload (annotate it with MediaPayload or apply "
        "@media_rail to have it screen media)"
    )
    logger.warning("%s", reason)
    if on_skip is not None:
        on_skip(reason)
    return None


__all__ = [
    "AudioPayload",
    "ImagePayload",
    "MediaKind",
    "MediaRailFn",
    "call_rail",
    "is_media_rail",
    "media_rail",
]
