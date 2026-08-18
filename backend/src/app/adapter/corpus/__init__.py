"""Seed document corpus — the hand-written knowledge the domain ships with.

This is **piece 9 of 10** of the adapter: a few small Markdown documents with simple
YAML-style frontmatter that :func:`load_seed_corpus` parses into typed
:class:`~app.adapter.schema.Document` records. The retrieval layer ingests these
alongside the LLM-generated corpus from :mod:`app.adapter.generator`.

Files are read through :mod:`importlib.resources`, so the loader is portable (no
absolute paths, works from a wheel or a checkout). To add a document, drop a new
``*.md`` file in this directory with the same frontmatter keys — no code change
needed. Swapping the domain means replacing these files wholesale.
"""

from __future__ import annotations

import importlib.resources as resources

from app.adapter.schema import Category, Document, DocumentKind


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a ``---``-delimited frontmatter block from the Markdown body.

    Args:
        text: The raw file contents.

    Returns:
        A tuple ``(fields, body)`` where ``fields`` maps frontmatter keys to raw
        string values and ``body`` is everything after the frontmatter.
    """
    if not text.lstrip().startswith("---"):
        return {}, text
    stripped = text.lstrip()
    _, _, rest = stripped.partition("---")
    block, _, body = rest.partition("---")
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, body.strip()


def _parse_tags(raw: str) -> list[str]:
    """Parse a ``[a, b, c]`` frontmatter list into a list of strings."""
    inner = raw.strip().strip("[]")
    return [t.strip() for t in inner.split(",") if t.strip()]


def _to_document(fields: dict[str, str], body: str, fallback_id: str) -> Document:
    """Build a :class:`Document` from parsed frontmatter and body."""
    category_raw = fields.get("category")
    category = Category(category_raw) if category_raw else None
    kind_raw = fields.get("kind", DocumentKind.KB_ARTICLE.value)
    return Document(
        id=fields.get("id", fallback_id),
        kind=DocumentKind(kind_raw),
        title=fields.get("title", fallback_id),
        body=body,
        category=category,
        tags=_parse_tags(fields.get("tags", "")),
        source="seed",
    )


def load_seed_corpus() -> list[Document]:
    """Load every seed ``*.md`` document in this package as a typed record.

    Returns:
        The seed documents, sorted by id for deterministic ordering.
    """
    documents: list[Document] = []
    package = resources.files(__package__)
    for entry in package.iterdir():
        if entry.name.endswith(".md"):
            fields, body = _parse_frontmatter(entry.read_text(encoding="utf-8"))
            documents.append(_to_document(fields, body, fallback_id=entry.name))
    return sorted(documents, key=lambda d: d.id)
