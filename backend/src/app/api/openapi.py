"""The HTTP contract: the ``/v1`` boundary, and the document that describes it.

Three things live here, and they are one idea.

**1. The version boundary.** Every product route is served under :data:`API_PREFIX`.
``aegis/PUBLIC.md`` makes a stability promise about the *Python* package; without a
version segment the HTTP API — the interface an integrator actually consumes — could
promise nothing, because there was no way to change it without breaking whoever was
already calling it.

**2. What deliberately stays unversioned.** :data:`INFRA_PATHS`. ``/health``, ``/ready``
and ``/readyz`` are not part of the product contract: they are what a load balancer,
a container orchestrator and the console's boot probe dial, they answer the same two
questions in every version, and a probe URL that moves when the API version moves is a
liveness check that silently starts 404-ing during a rollout. They are served at the
root and **nowhere else** — one path each, no ``/v1`` alias, so there is no ambiguity
about which one an operator should configure.

**3. The document.** :func:`build_openapi` is the served schema, and
``backend/openapi.json`` is a committed snapshot of it that
``tests/api/test_openapi_snapshot.py`` compares against. An API change that nobody
reviewed now fails CI instead of surprising an integrator — the same shape as
``web/src/config/graphTopology.json`` and ``docs/module/PIPELINES.md``, both of which
already work this way in this repo. That document is also the *input* to the generated
TypeScript client (``web/scripts/gen-api-types.mjs``), so the contract has exactly one
source and the console cannot drift from it by hand.

**The ``StreamEvent`` union is published here, and that is the point of §8.8.** It is
the product's primary interface — twenty variants streamed over ``POST /v1/query`` —
and it existed only as Pydantic classes plus a hand-written TypeScript mirror, so no
consumer outside this repo could validate a single frame. FastAPI cannot discover it on
its own: the endpoint returns an ``EventSourceResponse``, which is an opaque streaming
body as far as the framework is concerned. So the union is generated from the Pydantic
models and injected, and the ``200`` response of ``POST /v1/query`` is re-declared as
``text/event-stream`` carrying it.

Generated in ``mode="serialization"``, deliberately: an event is only ever *sent*, and
the two modes genuinely differ here — ``GraphNode.owners`` carries ``exclude=True``, so
it is a validation-time input and never appears on the wire. Publishing the validation
schema would have described a field no client will ever see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from app.api.schemas import StreamEvent

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from fastapi import FastAPI

__all__ = [
    "API_PREFIX",
    "INFRA_PATHS",
    "SSE_MEDIA_TYPE",
    "STREAM_EVENT_SCHEMA_NAME",
    "build_openapi",
    "stream_event_schemas",
]

#: The version segment every product route is served under.
API_PREFIX = "/v1"

#: The probes that stay at the root, unversioned, and are served at exactly one path.
#: Infrastructure dials these; they are not part of the versioned product surface.
INFRA_PATHS = frozenset({"/health", "/ready", "/readyz"})

#: The media type ``POST /v1/query`` actually answers with.
SSE_MEDIA_TYPE = "text/event-stream"

#: The component name the published union takes in ``components.schemas``.
STREAM_EVENT_SCHEMA_NAME = "StreamEvent"

#: The one operation whose body is the event stream.
_QUERY_OPERATION = (f"{API_PREFIX}/query", "post")

_STREAM_DESCRIPTION = """\
A Server-Sent Events stream of `StreamEvent`s, one per frame.

Each frame carries a `data:` line holding a JSON-encoded `StreamEvent`. **The
discriminant is `type`, inside that payload** — the `event:` line duplicates it and a
client should parse `data` and ignore it. Frames are separated by a blank line, which
`sse-starlette` writes as `\\r\\n\\r\\n`.
"""


def stream_event_schemas() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return the published ``StreamEvent`` union and every schema it references.

    Returns:
        A ``(union, definitions)`` pair. ``union`` is the ``oneOf`` + ``discriminator``
        document that belongs at ``components.schemas.StreamEvent``; ``definitions``
        are the twenty variants and their nested models, keyed by component name.
    """
    schema = TypeAdapter(StreamEvent).json_schema(
        ref_template="#/components/schemas/{model}", mode="serialization"
    )
    definitions = dict(schema.pop("$defs", {}))
    schema["title"] = STREAM_EVENT_SCHEMA_NAME
    schema["description"] = (
        "Any event a run may emit over the POST /v1/query SSE stream. Discriminated on "
        "the `type` field carried inside the frame's `data` payload."
    )
    return schema, definitions


def _inject_stream_event(schema: dict[str, Any]) -> None:
    """Add the ``StreamEvent`` union to ``components`` and to ``POST /v1/query``.

    Idempotent, because :meth:`fastapi.FastAPI.openapi` caches its document and this
    runs on every call.

    Raises:
        ValueError: When a variant's component name is already taken by a *different*
            schema. Silently overwriting one would publish a description of something
            other than what the server sends, which is the whole failure this document
            exists to prevent.
    """
    components: dict[str, Any] = schema.setdefault("components", {}).setdefault(
        "schemas", {}
    )
    if STREAM_EVENT_SCHEMA_NAME in components:
        return

    union, definitions = stream_event_schemas()
    for name, definition in definitions.items():
        existing = components.get(name)
        if existing is None:
            components[name] = definition
        elif existing != definition:
            raise ValueError(
                f"component schema {name!r} means two different things: the request/"
                f"response models and the StreamEvent union disagree about it."
            )
    components[STREAM_EVENT_SCHEMA_NAME] = union

    path, method = _QUERY_OPERATION
    operation = schema["paths"][path][method]
    operation["responses"]["200"] = {
        "description": _STREAM_DESCRIPTION,
        "content": {
            SSE_MEDIA_TYPE: {
                "schema": {"$ref": f"#/components/schemas/{STREAM_EVENT_SCHEMA_NAME}"}
            }
        },
    }


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """Return the served OpenAPI document, ``StreamEvent`` included.

    Args:
        app: The application to describe.

    Returns:
        The document FastAPI generates, plus the published event union. FastAPI caches
        it on ``app.openapi_schema``; this mutates and returns that same object, so the
        injection happens once however often the document is requested.
    """
    from fastapi import FastAPI as _FastAPI

    schema = _FastAPI.openapi(app)
    _inject_stream_event(schema)
    return schema
