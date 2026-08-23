"""Standard-format SBOM export — CycloneDX 1.6 and SPDX 2.3, from the live install.

``GET /stack`` already answers "what is this platform made of" for a human reading a
console. This module answers the same question for a **machine somebody else owns**:
a buyer's security team feeds a CycloneDX or SPDX document to their own scanner, and
an in-house JSON shape — however honest — is a document they have to write a parser
for before they can believe any of it. OWASP LLM03 asks for exactly this artefact, and
its absence was the second half of that row's gap.

**Resolved, never authored.** The component list is every distribution
:func:`importlib.metadata.distributions` can see in the running interpreter — the same
source :mod:`app.platform.stack` uses for its curated view, with the curation removed.
A hand-maintained export drifts from the environment it claims to describe on the first
``uv add``, and an SBOM that is wrong is worse than no SBOM, because a scanner believes
it.

**What each format is for**, since shipping two needs a reason:

* **CycloneDX 1.6** (``application/vnd.cyclonedx+json``) is what vulnerability tooling
  consumes — Dependency-Track, Grype, Trivy — because it carries PURLs, and a PURL is
  what an advisory database joins on.
* **SPDX 2.3** (``application/spdx+json``) is what procurement and licence review
  consume, and what US federal guidance (EO 14028 / NTIA minimum elements) names.

Both are generated from **one** :func:`resolve_inventory` pass, so the two documents
cannot describe different machines.

**What is honestly missing**, stated here rather than implied: neither document is
signed, and there is no in-toto/SLSA provenance attestation for the build that produced
this environment. What is present is the integrity evidence this repository does own —
``uv.lock``'s 4,219 sha256 digests — and the export records how many of them backed the
resolve, so a reader can tell a locked install from a loose one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

__all__ = [
    "CYCLONEDX_SPEC_VERSION",
    "SPDX_VERSION",
    "Component",
    "build_cyclonedx",
    "build_spdx",
    "lockfile_digest_count",
    "resolve_inventory",
]

#: The CycloneDX specification this export claims to conform to.
CYCLONEDX_SPEC_VERSION = "1.6"

#: The SPDX specification this export claims to conform to.
SPDX_VERSION = "SPDX-2.3"

#: Lockfiles whose hash pins back the resolve. Both are in this repository.
_LOCKFILES: tuple[str, ...] = ("backend/uv.lock", "aegis/uv.lock")

#: A ``sha256:...`` pin as uv writes it. Counted, not parsed further — the number is
#: the claim ("this resolve is hash-pinned"), and the lockfile is the evidence.
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

#: SPDX requires every id to match this. A distribution name may not.
_SPDX_ID_UNSAFE = re.compile(r"[^A-Za-z0-9.\-]")


def _repo_root() -> Path:
    """Return the repository root, walking up from this file."""
    return Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class Component:
    """One installed distribution, in the neutral shape both formats project from.

    Attributes:
        name: The distribution name as the environment reports it.
        version: The installed version.
        license_id: The declared licence, best-effort — ``License-Expression`` when
            the distribution uses PEP 639, else the ``License`` field, else the
            licence classifier. Empty when the distribution declares none, which is
            reported as ``NOASSERTION`` rather than guessed.
        purl: The Package URL — ``pkg:pypi/<name>@<version>``. This is the join key
            every advisory database and SBOM scanner uses.
        author: The declared author/maintainer, best-effort; empty when absent.
        homepage: The project URL, best-effort; empty when absent.
    """

    name: str
    version: str
    license_id: str
    purl: str
    author: str = ""
    homepage: str = ""


def _license_of(meta: metadata.PackageMetadata) -> str:
    """Best-effort licence for one distribution, in declaration order of authority."""
    expression = meta.get("License-Expression")
    if expression:
        return str(expression).strip()
    declared = meta.get("License")
    if declared and len(str(declared)) < 120 and "\n" not in str(declared):
        # A short one-liner is an identifier; a long value is the licence text itself
        # pasted into the field, which is not an identifier and must not be reported
        # as one.
        return str(declared).strip()
    for classifier in meta.get_all("Classifier") or ():
        if str(classifier).startswith("License :: "):
            return str(classifier).rsplit(" :: ", 1)[-1].strip()
    return ""


def _homepage_of(meta: metadata.PackageMetadata) -> str:
    """Best-effort project URL — ``Home-page``, else the first ``Project-URL``."""
    home = meta.get("Home-page")
    if home:
        return str(home).strip()
    for entry in meta.get_all("Project-URL") or ():
        _, _, url = str(entry).partition(",")
        if url.strip():
            return url.strip()
    return ""


def resolve_inventory() -> list[Component]:
    """Return every distribution installed in the running interpreter, sorted by name.

    Returns:
        One :class:`Component` per distribution that reports a name and a version.
        A distribution whose metadata cannot be read is skipped with a debug log
        rather than aborting the export — a single broken ``.dist-info`` must not
        cost a buyer the whole document.
    """
    components: dict[str, Component] = {}
    for dist in metadata.distributions():
        try:
            meta = dist.metadata
            name = (meta["Name"] or "").strip()
            version = (dist.version or "").strip()
        except Exception:  # noqa: BLE001 - one broken dist-info must not kill the export
            logger.debug("Skipping a distribution whose metadata could not be read.")
            continue
        if not name or not version:
            continue
        # A duplicate name (two .dist-info directories on the path) is resolved to the
        # first one seen, which is the one import would win with.
        if name.lower() in components:
            continue
        components[name.lower()] = Component(
            name=name,
            version=version,
            license_id=_license_of(meta),
            purl=f"pkg:pypi/{quote(name.lower())}@{quote(version)}",
            author=str(meta.get("Author") or meta.get("Author-email") or "").strip(),
            homepage=_homepage_of(meta),
        )
    return sorted(components.values(), key=lambda c: c.name.lower())


def lockfile_digest_count() -> int:
    """Return how many ``sha256:`` pins the repository's lockfiles carry.

    Zero means the lockfiles were not found (a wheel-only deployment, a sdist without
    the repository around it), and the export says ``0`` rather than omitting the
    field — "we could not find the lockfiles" and "the resolve is unpinned" must not
    render identically.
    """
    root = _repo_root()
    total = 0
    for relative in _LOCKFILES:
        path = root / relative
        try:
            total += len(_DIGEST.findall(path.read_text(encoding="utf-8")))
        except OSError:
            logger.debug("Lockfile %s not readable from %s.", relative, root)
    return total


def _spdx_id(name: str) -> str:
    """Return an SPDX-legal element id for a distribution name."""
    return f"SPDXRef-Package-{_SPDX_ID_UNSAFE.sub('-', name)}"


def build_cyclonedx(components: list[Component] | None = None) -> dict[str, object]:
    """Build a CycloneDX 1.6 JSON document for the live install.

    Args:
        components: Pre-resolved inventory; resolved live when ``None``.

    Returns:
        A CycloneDX 1.6 ``bom`` document as a plain dict, ready to serialise. Every
        component carries a PURL, which is what a vulnerability scanner joins on.
    """
    resolved = components if components is not None else resolve_inventory()
    now = datetime.now(UTC).isoformat()
    digests = lockfile_digest_count()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "version": 1,
        "metadata": {
            "timestamp": now,
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "aegis-sbom",
                        "version": "1.0",
                        "description": (
                            "Resolved from importlib.metadata in the running "
                            "interpreter, not from a maintained list."
                        ),
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "aegis",
                "version": "0.1.0",
                "description": "Aegis agentic platform (backend + aegis core).",
            },
            "properties": [
                {
                    "name": "aegis:lockfile_sha256_pins",
                    "value": str(digests),
                },
                {
                    "name": "aegis:attestation",
                    "value": (
                        "unsigned; no in-toto/SLSA provenance. Integrity evidence is "
                        "the lockfile hash pins counted above."
                    ),
                },
            ],
        },
        "components": [
            {
                "type": "library",
                "bom-ref": c.purl,
                "name": c.name,
                "version": c.version,
                "purl": c.purl,
                "licenses": (
                    [{"license": {"name": c.license_id}}] if c.license_id else []
                ),
                **({"author": c.author} if c.author else {}),
                **(
                    {"externalReferences": [{"type": "website", "url": c.homepage}]}
                    if c.homepage
                    else {}
                ),
            }
            for c in resolved
        ],
    }


def build_spdx(components: list[Component] | None = None) -> dict[str, object]:
    """Build an SPDX 2.3 JSON document for the live install.

    Args:
        components: Pre-resolved inventory; resolved live when ``None``.

    Returns:
        An SPDX 2.3 document as a plain dict. Licences the environment does not
        declare are ``NOASSERTION`` — the SPDX word for "we did not determine this" —
        never a guess, because a wrong licence identifier in a procurement review is a
        legal claim.
    """
    resolved = components if components is not None else resolve_inventory()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    packages = [
        {
            "SPDXID": _spdx_id(c.name),
            "name": c.name,
            "versionInfo": c.version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": c.license_id or "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "supplier": f"Person: {c.author}" if c.author else "NOASSERTION",
            "externalRefs": [
                {
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": c.purl,
                }
            ],
        }
        for c in resolved
    ]
    return {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "aegis-sbom",
        "documentNamespace": f"https://aegis.invalid/spdx/{now}",
        "creationInfo": {
            "created": now,
            "creators": ["Tool: aegis-sbom-1.0"],
            "comment": (
                "Resolved from importlib.metadata in the running interpreter. "
                f"{lockfile_digest_count()} sha256 pins back the resolve. The "
                "document is unsigned and carries no SLSA/in-toto provenance."
            ),
        },
        "packages": packages,
        "relationships": [
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": p["SPDXID"],
            }
            for p in packages
        ],
    }
