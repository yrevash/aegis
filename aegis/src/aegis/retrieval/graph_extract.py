"""Real entity/relation extraction for the databaseless knowledge graph.

This is what makes the lite backend's graph *genuine* rather than a decorative chain
of source-document nodes. It turns raw chunk text into typed **entities** and real,
**labelled relations**, so the same "ACME Corp" mentioned across twenty chunks becomes
one node (merged by a normalised id) that connects those chunks — the structure a
knowledge graph is supposed to have.

Two interchangeable extractors satisfy the :class:`Extractor` protocol:

* :class:`LLMCachedExtractor` (primary) — one extraction prompt per chunk via an
  injected async ``complete`` callable, its result **cached to disk** keyed by
  ``sha256(chunk_text)``. After the first run the cache replays with **no LLM call and
  no network** (offline), and a restart or re-ingest reuses it. Fail-soft: any completer
  error or unparseable output yields empty extraction (never a crash).
* :class:`SpacyExtractor` (deterministic fallback) — spaCy ``en_core_web_sm`` NER mapped
  onto the same typed vocabulary, with relations drawn from intra-sentence co-occurrence
  (labelled by the connecting root verb, else ``"co-occurs"``). If spaCy or its model is
  missing, extraction degrades to a logged :class:`NoOpExtractor` rather than failing.

Honesty by construction: every entity comes from real corpus text; every relation
carries a real extracted phrase (never a constant placeholder); nothing is fabricated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from aegis.core.models import ModelRole
from aegis.retrieval.lightrag_backend import _ENTITY_TYPES
from aegis.retrieval.protocols import CompleteFn

_log = logging.getLogger(__name__)

#: The stable, domain-agnostic entity-type vocabulary — reused verbatim from the
#: LightRAG backend so the lite graph and the full graph speak the *same* node types.
ENTITY_TYPES: tuple[str, ...] = _ENTITY_TYPES
_ENTITY_TYPE_SET: frozenset[str] = frozenset(ENTITY_TYPES)

#: Catch-all bucket (a real vocabulary member) for an entity type the extractor emitted
#: outside the vocabulary — keeps the viz palette total without inventing a new kind.
_FALLBACK_KIND = "category"

#: spaCy NER label → our vocabulary. Labels that carry no useful *entity* identity
#: (DATE/TIME/MONEY/PERCENT/CARDINAL/ORDINAL/QUANTITY) are deliberately absent, so they
#: are dropped rather than mislabelled.
_SPACY_LABEL_MAP: dict[str, str] = {
    "ORG": "organization",
    "PERSON": "person",
    "PRODUCT": "product",
    "EVENT": "event",
    "LAW": "policy",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "NORP": "category",
    "WORK_OF_ART": "category",
    "LANGUAGE": "category",
}

_WS = re.compile(r"\s+")


def _normalize(label: str) -> str:
    """Lowercase and whitespace-collapse ``label`` for cross-chunk dedup/merge."""
    return _WS.sub(" ", label.strip().lower())


def _coerce_kind(kind: str) -> str:
    """Map an extractor-supplied type onto the vocabulary (catch-all if unknown)."""
    k = _normalize(kind)
    return k if k in _ENTITY_TYPE_SET else _FALLBACK_KIND


@dataclass(frozen=True)
class Entity:
    """A typed graph entity, identified by ``kind:normalized_label`` for merging.

    Two mentions of the same name and kind — anywhere in the corpus — produce the same
    :attr:`id`, so the backend merges them into one node. ``label`` keeps a human
    surface form for display; identity is carried entirely by :attr:`id`.
    """

    id: str
    label: str
    kind: str

    @classmethod
    def make(cls, label: str, kind: str) -> Entity:
        """Build an entity, normalising ``kind`` to the vocabulary and deriving the id."""
        clean_kind = _coerce_kind(kind)
        norm = _normalize(label)
        return cls(id=f"{clean_kind}:{norm}", label=label.strip(), kind=clean_kind)


@dataclass(frozen=True)
class Relation:
    """A directed, labelled edge between two entity ids.

    ``phrase`` is the *real* connecting text an extractor produced (a verb phrase or
    relationship description) — never a hardcoded constant, so an edge always says
    something the corpus actually stated.
    """

    src_id: str
    tgt_id: str
    phrase: str


@runtime_checkable
class Extractor(Protocol):
    """Turns a chunk's text into typed entities and labelled relations."""

    #: Honest identifier of which extractor actually ran (for provenance/logging).
    name: str

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return the entities and relations found in ``chunk_text``."""
        ...


def _label_in_text(text: str, label: str) -> bool:
    """True iff ``label`` occurs literally (case-insensitively) in ``text``.

    Word-boundary lookarounds keep ``"ACME"`` from matching inside ``"ACMEXYZ"`` while
    still allowing multi-word / punctuated labels. This is the **verifiable** basis of an
    entity→chunk mention: a mention exists only when the surface form is really present.
    """
    norm = label.strip()
    if not norm:
        return False
    pattern = r"(?<!\w)" + re.escape(norm) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def find_mentions(text: str, entities: Iterable[Entity]) -> set[str]:
    """Return the ids of ``entities`` whose label literally appears in ``text``."""
    return {e.id for e in entities if _label_in_text(text, e.label)}


# ─────────────────────────────────────────────────────────────────────────────
# LLM-cached extractor (primary)
# ─────────────────────────────────────────────────────────────────────────────

_LLM_SYSTEM = (
    "You extract a small knowledge graph from a single passage of text. "
    "Respond with ONLY a JSON object, no prose, no code fences."
)

_LLM_INSTRUCTION = (
    "Extract the salient entities and the relationships stated between them.\n"
    "Choose each entity's type from exactly this vocabulary: "
    + ", ".join(ENTITY_TYPES)
    + ".\n"
    'Return JSON of the form: {"entities": [{"name": "...", "type": "..."}], '
    '"relations": [{"source": "<entity name>", "target": "<entity name>", '
    '"relation": "<short verb phrase>"}]}.\n'
    "Only use entity names that appear verbatim in the passage. Only include a relation "
    "the passage actually states, with a concrete relation phrase. If there is nothing to "
    "extract, return empty arrays.\n\nPassage:\n"
)


def _cache_key(text: str) -> str:
    """Content-address a chunk's text for cache lookup (stable across processes)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _strip_fences(raw: str) -> str:
    """Best-effort strip of markdown code fences around a JSON blob."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    return s


def _parse_llm(raw: str) -> tuple[list[Entity], list[Relation]]:
    """Parse an LLM extraction response into entities + relations (fail-soft)."""
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        _log.warning("graph_extract: unparseable LLM extraction output; skipping chunk")
        return ([], [])
    if not isinstance(data, dict):
        return ([], [])

    entities: dict[str, Entity] = {}
    name_to_id: dict[str, str] = {}
    for raw_ent in data.get("entities") or []:
        if not isinstance(raw_ent, dict):
            continue
        name = str(raw_ent.get("name") or "").strip()
        if not name:
            continue
        ent = Entity.make(name, str(raw_ent.get("type") or _FALLBACK_KIND))
        entities.setdefault(ent.id, ent)
        name_to_id[_normalize(name)] = ent.id

    relations: list[Relation] = []
    for raw_rel in data.get("relations") or []:
        if not isinstance(raw_rel, dict):
            continue
        src = name_to_id.get(_normalize(str(raw_rel.get("source") or "")))
        tgt = name_to_id.get(_normalize(str(raw_rel.get("target") or "")))
        phrase = str(raw_rel.get("relation") or "").strip()
        if not src or not tgt or src == tgt or not phrase:
            continue  # no fabricated edges: need both endpoints and a real phrase
        relations.append(Relation(src_id=src, tgt_id=tgt, phrase=phrase))

    return (list(entities.values()), relations)


class LLMCachedExtractor:
    """Extract via one cached LLM prompt per chunk; replay offline from disk.

    The first extraction of a given chunk text calls ``complete`` and writes the parsed
    result to ``{working_dir}/graph_extract/{sha256}.json``. Every subsequent extraction
    of that exact text — this run, a re-ingest, or after a restart — is served from that
    file with no LLM call and no network. With ``complete=None`` the extractor is a pure
    cache replayer (useful for offline runs once the cache is warm).
    """

    name = "llm-cached"

    def __init__(
        self,
        complete: CompleteFn | None,
        working_dir: str | Path,
        *,
        role: ModelRole = ModelRole.CHEAP,
    ) -> None:
        """Store the completer and the on-disk cache directory."""
        self._complete = complete
        self._dir = Path(working_dir) / "graph_extract"
        self._role = role

    def _cache_path(self, text: str) -> Path:
        return self._dir / f"{_cache_key(text)}.json"

    def _read_cache(self, text: str) -> tuple[list[Entity], list[Relation]] | None:
        path = self._cache_path(text)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            entities = [
                Entity(id=e["id"], label=e["label"], kind=e["kind"])
                for e in payload.get("entities", [])
            ]
            relations = [
                Relation(src_id=r["src_id"], tgt_id=r["tgt_id"], phrase=r["phrase"])
                for r in payload.get("relations", [])
            ]
            return (entities, relations)
        except (OSError, ValueError, KeyError, TypeError):
            _log.warning("graph_extract: corrupt cache at %s; re-extracting", path)
            return None

    def _write_cache(
        self, text: str, entities: list[Entity], relations: list[Relation]
    ) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "entities": [
                    {"id": e.id, "label": e.label, "kind": e.kind} for e in entities
                ],
                "relations": [
                    {"src_id": r.src_id, "tgt_id": r.tgt_id, "phrase": r.phrase}
                    for r in relations
                ],
            }
            self._cache_path(text).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # a cache write failure must never break ingestion
            _log.warning("graph_extract: could not write cache: %s", exc)

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return cached extraction, else call the LLM once and cache the result."""
        cached = self._read_cache(chunk_text)
        if cached is not None:
            return cached
        if self._complete is None:
            return ([], [])  # offline replay with a cold cache: honest empty, no network
        messages: list[dict[str, object]] = [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": _LLM_INSTRUCTION + chunk_text},
        ]
        try:
            result = await self._complete(self._role, messages, temperature=0.0)
            entities, relations = _parse_llm(result.content)
        except Exception as exc:  # fail-soft: extraction never crashes ingestion
            _log.warning("graph_extract: LLM extraction failed: %s", exc)
            return ([], [])
        self._write_cache(chunk_text, entities, relations)
        return (entities, relations)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic spaCy fallback
# ─────────────────────────────────────────────────────────────────────────────

_NLP: object | None = None
_SPACY_TRIED = False


def _load_spacy() -> object | None:
    """Lazily load spaCy ``en_core_web_sm`` once; return ``None`` if unavailable."""
    global _NLP, _SPACY_TRIED
    if _SPACY_TRIED:
        return _NLP
    _SPACY_TRIED = True
    try:
        import spacy  # lazy: optional dependency

        _NLP = spacy.load("en_core_web_sm")
    except Exception as exc:  # missing package or model → degrade, do not crash
        _log.warning("graph_extract: spaCy unavailable (%s); extraction disabled", exc)
        _NLP = None
    return _NLP


class SpacyExtractor:
    """Deterministic NER-based extractor: typed entities + co-occurrence relations."""

    name = "spacy"

    def __init__(self, nlp: object) -> None:
        """Hold a loaded spaCy pipeline (see :func:`_load_spacy`)."""
        self._nlp = nlp

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Map NER spans to typed entities; relate entities sharing a sentence."""
        doc = self._nlp(chunk_text)  # type: ignore[operator]
        entities: dict[str, Entity] = {}
        relations: set[Relation] = set()

        for sent in doc.sents:
            in_sentence: list[Entity] = []
            seen_ids: set[str] = set()
            for span in sent.ents:
                kind = _SPACY_LABEL_MAP.get(span.label_)
                if kind is None:
                    continue  # a non-entity NER label (dates, money, …): drop it
                ent = Entity.make(span.text, kind)
                entities.setdefault(ent.id, ent)
                if ent.id not in seen_ids:
                    seen_ids.add(ent.id)
                    in_sentence.append(ent)

            if len(in_sentence) < 2:
                continue
            root = getattr(sent, "root", None)
            phrase = (
                root.lemma_ if root is not None and root.pos_ == "VERB" else "co-occurs"
            )
            for i in range(len(in_sentence)):
                for j in range(i + 1, len(in_sentence)):
                    relations.add(
                        Relation(
                            src_id=in_sentence[i].id,
                            tgt_id=in_sentence[j].id,
                            phrase=phrase,
                        )
                    )

        return (list(entities.values()), list(relations))


class NoOpExtractor:
    """Extracts nothing — the honest degraded mode when no extractor is available."""

    name = "noop"

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return no entities and no relations."""
        return ([], [])


def build_extractor(
    *,
    complete: CompleteFn | None = None,
    working_dir: str | Path = "rag_storage",
    prefer: str = "llm",
) -> Extractor:
    """Pick the best available extractor.

    LLM-cached when a completer is supplied and ``prefer == "llm"`` (offline after the
    first run via its disk cache); otherwise the deterministic spaCy extractor; and if
    spaCy/its model are missing, a logged no-op. The returned object's ``name`` is honest
    about which one actually ran.
    """
    if prefer == "llm" and complete is not None:
        return LLMCachedExtractor(complete, working_dir)
    nlp = _load_spacy()
    if nlp is not None:
        return SpacyExtractor(nlp)
    return NoOpExtractor()
