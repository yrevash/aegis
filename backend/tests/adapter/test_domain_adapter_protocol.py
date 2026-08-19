"""The reference adapter against the executable contract, :class:`aegis.adapter.DomainAdapter`.

``test_piece_manifest.py`` counts the pieces on disk; this counts the pieces the *core*
can actually reach. The two are different questions, and the difference is exactly how
``memory_spec`` went missing: the file was on disk and in the manifest, and nothing
imported it, so ``app.adapter.memory_spec`` was not an attribute of the adapter package
at all — nine of ten pieces reachable, with no error anywhere to say which one was not.
"""

from __future__ import annotations

from types import SimpleNamespace

from aegis.adapter import DomainAdapter, adapter_members, missing_members
from test_piece_manifest import EXPECTED_DIRS, EXPECTED_MODULES

import app.adapter

#: The identity attributes: not pieces, but required members. ``DOMAIN_DESCRIPTION`` is
#: wired straight into the guardrails' topical rail as ``allowed_topics``, so it is a
#: control input rather than metadata.
IDENTITY = frozenset({"DOMAIN_ID", "DOMAIN_DESCRIPTION"})


def test_the_reference_adapter_satisfies_the_protocol():
    """Every member the core reaches for is reachable on ``app.adapter``."""
    assert missing_members(app.adapter) == []
    assert isinstance(app.adapter, DomainAdapter)


def test_the_protocol_covers_the_pieces_the_manifest_counts():
    """The Protocol's members and the manifest's pieces cannot drift apart.

    Nine members for ten pieces: the eight modules, plus ``corpus/``. ``skills/`` is
    deliberately *not* a member — it is a directory of Markdown discovered at call time,
    and it is already named by ``memory_spec.SKILLS_DIR``, so a second top-level
    spelling would recreate the "is it five or six?" ambiguity the Protocol exists to
    end. Add a ninth module and this fails until the Protocol carries it.
    """
    members = set(adapter_members()) - IDENTITY
    modules = {name.removesuffix(".py") for name in EXPECTED_MODULES}

    assert members == modules | {"corpus"}
    assert EXPECTED_DIRS - members == {"skills"}
    assert app.adapter.memory_spec.SKILLS_DIR.endswith("skills")


def test_an_adapter_missing_a_piece_is_named_not_guessed_at():
    """The check can fail, and it says which piece is absent.

    This is the state the reference adapter was in before this task: everything present
    except one module nobody had imported.
    """
    incomplete = SimpleNamespace(
        **{name: object() for name in adapter_members() if name != "memory_spec"}
    )

    assert missing_members(incomplete) == ["memory_spec"]
    assert not isinstance(incomplete, DomainAdapter)
