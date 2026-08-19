"""The TypeScript ``StreamEvent`` union must match the Python one exactly.

``web/src/lib/stream.ts`` hand-mirrors :data:`app.api.schemas.StreamEvent`, and that
union is what ``POST /query`` actually streams. It is the protocol the product runs on.

The repo already had a mirror test — ``test_stream_name_mirror.py`` — but it guards
``web/src/lib/streamNames.ts``, the AG-UI ``CustomEvent`` name table, which **nothing in
``web/src`` imports**: its only reader is that test. So the protocol nobody uses had a
parity guard and the protocol serving the console had none, and it drifted exactly as an
unguarded mirror does. Three variants the backend emits (``reflection``, ``routing``,
``memory``) never reached the TypeScript union at all, so the reducer's ``default``
branch silently discarded the self-repair loop, the supervisor hand-off and every memory
recall — three of the most demoable things the system does, on the wire and invisible.

Two comparisons, because drift arrives in two shapes:

1. **Variants** — a ``type`` literal on one side and not the other. This is the one that
   made the console blind.
2. **Fields** — a variant present on both sides whose payload disagrees. This is the
   quieter one: ``agent_id`` was added to ``_BaseEvent`` in ``6af14f6`` precisely because
   pydantic dropped it *silently*, and a mirror that only counted variants would not have
   noticed the TypeScript side lacking it either.

Backend-side rather than in ``aegis/`` for the same reason as its sibling: the importable
core must not know a web console exists.
"""

from __future__ import annotations

import re
import types
from pathlib import Path
from typing import Annotated, Union, get_args, get_origin

import pytest

from app.api.schemas import StreamEvent

#: Repo root, from ``backend/tests/api/`` → ``backend/`` → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIRROR = _REPO_ROOT / "web" / "src" / "lib" / "stream.ts"

#: ``export interface Name extends BaseEvent {`` … up to the closing brace at column 0.
_INTERFACE = re.compile(
    r"^export interface (\w+) extends BaseEvent \{(.*?)^\}", re.M | re.S
)
#: ``  type: 'run_started'`` — the discriminant literal inside one interface body.
_DISCRIMINANT = re.compile(r"^\s*type:\s*'([a-z0-9_]+)'", re.M)
#: ``  name?: T`` / ``  name: T`` — one declared property of an interface body. Comment
#: lines are stripped first, so a ``*  foo: bar`` inside a docstring cannot match.
_PROPERTY = re.compile(r"^  (\w+)\??:", re.M)


def _python_variants() -> dict[str, frozenset[str]]:
    """Return ``{type literal: field names}`` for every member of the Python union."""
    union = StreamEvent
    if get_origin(union) is Annotated:
        union = get_args(union)[0]
    # ``A | B`` is ``types.UnionType``; ``Union[A, B]`` is ``typing.Union``. The
    # schema module writes the former, but accepting both means a stylistic rewrite
    # of the union cannot silently reduce this test to a single-member no-op.
    is_union = get_origin(union) in (Union, types.UnionType)
    members = get_args(union) if is_union else (union,)
    out: dict[str, frozenset[str]] = {}
    for model in members:
        literal = get_args(model.model_fields["type"].annotation)[0]
        out[literal] = frozenset(model.model_fields)
    return out


def _strip_comments(text: str) -> str:
    """Blank out ``/* … */`` and ``// …`` so a doc comment cannot look like a field."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _typescript_variants() -> dict[str, frozenset[str]]:
    """Return ``{type literal: field names}`` for every member of the TS union.

    Only the interfaces that are **named in the exported ``StreamEvent`` union** count.
    An interface that extends ``BaseEvent`` but was left out of the union is not on the
    wire as far as the client is concerned, and counting it would let exactly the bug
    this test exists to catch pass.
    """
    source = _strip_comments(_MIRROR.read_text(encoding="utf-8"))
    union_block = re.search(
        r"export type StreamEvent =\n((?:\s*\|\s*\w+\n)+)", source
    )
    assert union_block is not None, f"no `export type StreamEvent =` union in {_MIRROR}"
    named = set(re.findall(r"\|\s*(\w+)", union_block.group(1)))

    base = re.search(r"^export interface BaseEvent \{(.*?)^\}", source, re.M | re.S)
    assert base is not None, f"no `BaseEvent` interface in {_MIRROR}"
    base_fields = frozenset(_PROPERTY.findall(base.group(1)))

    out: dict[str, frozenset[str]] = {}
    for name, body in _INTERFACE.findall(source):
        if name not in named:
            continue
        discriminant = _DISCRIMINANT.search(body)
        assert discriminant is not None, f"{name} declares no `type: '…'` literal"
        out[discriminant.group(1)] = base_fields | frozenset(_PROPERTY.findall(body))
    return out


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_typescript_union_carries_every_python_variant() -> None:
    """Every ``StreamEvent`` variant is on both sides, and nothing extra is on either."""
    python, typescript = _python_variants(), _typescript_variants()

    missing = sorted(set(python) - set(typescript))
    extra = sorted(set(typescript) - set(python))

    assert not missing, (
        f"web/src/lib/stream.ts is missing {missing}. The backend streams these over "
        f"POST /query and the reducer's `default` branch throws them away silently."
    )
    assert not extra, (
        f"web/src/lib/stream.ts declares {extra}, which app.api.schemas.StreamEvent "
        f"does not. The console handles an event no run can ever emit — which is how "
        f"an `abstained` phase survived in the reducer with no producer behind it."
    )


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_each_shared_variant_declares_the_same_fields() -> None:
    """A variant on both sides must carry the same payload on both sides."""
    python, typescript = _python_variants(), _typescript_variants()
    drift = {
        name: (sorted(python[name] - typescript[name]), sorted(typescript[name] - python[name]))
        for name in sorted(set(python) & set(typescript))
        if python[name] != typescript[name]
    }
    assert not drift, (
        "these StreamEvent variants disagree between Python and stream.ts "
        f"(missing in TS, extra in TS): {drift}"
    )


@pytest.mark.skipif(not _MIRROR.exists(), reason="web console not present in this checkout")
def test_the_parser_actually_finds_variants() -> None:
    """Guard the guard: an empty parse would make both tests above vacuously pass.

    The same failure mode the deleted ``STREAM_NAME_COUNT`` check had — a check whose
    subject can silently become empty proves nothing when it passes.
    """
    assert len(_python_variants()) >= 15
    assert len(_typescript_variants()) >= 15
