"""Dependency vulnerability verdicts from OSV.dev — the advisory feed LLM03 lacked.

:mod:`app.platform.patches` answers "is this package behind the registry?", which is
**freshness**, not a vulnerability verdict: a package can be three releases behind and
carry no advisory, and it can be on the newest release and carry four. Reading the
first as the second is the specific overclaim the LLM03 row refused to make, and this
module is what closes it.

**The source is OSV.dev**, Google's open vulnerability database, queried live over its
public API. It is the aggregator behind ``pip-audit``: it federates GHSA, PYSEC, the
NVD's CVE records and the ecosystem-native advisories, and it answers on a
``(ecosystem, name, version)`` triple, which is exactly what
:func:`app.platform.sbom.resolve_inventory` already produces from the running
interpreter. No advisory list is vendored into this repository — a snapshot of a
vulnerability database is out of date the day it is committed, and a stale "clean"
verdict is worse than none.

**Honesty rule — no clean bill of health without a real answer** (the same rule
:mod:`app.platform.patches` holds itself to, for the same reason). A package is
``clean`` only after OSV actually answered for it. A network failure, a timeout or a
malformed body yields ``unknown`` with a note saying so, and the whole audit reports
``online=False`` when nothing got through. ``passed`` is ``False`` whenever any package
is vulnerable **or** any package is unknown, so an audit that could not run cannot be
read as an audit that found nothing.

**Two calls per audit at most, plus details.** Versions are batched through
``POST /v1/querybatch`` (100 at a time), which returns advisory *ids* only; the
human-readable half — summary, severity, the CVE alias a reader recognises — needs a
``GET /v1/vulns/{id}`` per distinct id, so those are capped at
:data:`_MAX_DETAIL_FETCHES` and the remainder are reported by id with severity
``unknown``. A cap that is visible in the payload beats an audit that takes four
minutes and gets cancelled.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.platform.sbom import Component, resolve_inventory

logger = logging.getLogger(__name__)

__all__ = [
    "OSV_BATCH_URL",
    "AdvisoryAudit",
    "PackageAdvisories",
    "Vulnerability",
    "audit_dependencies",
]

#: OSV's batched query endpoint — ``(ecosystem, name, version)`` in, advisory ids out.
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"

#: Per-advisory detail, for the summary/severity a person can read.
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{vuln_id}"

#: Wall-clock budget for one HTTP call to OSV.
_TIMEOUT_S = 10.0

#: How many versions go in one batch. OSV documents 1,000; 100 keeps each response
#: small enough that one slow batch does not stall the whole audit.
_BATCH_SIZE = 100

#: Cap on ``GET /v1/vulns/{id}`` calls per audit. Beyond it, advisories are reported
#: by id with ``severity="unknown"`` and a note — never dropped.
_MAX_DETAIL_FETCHES = 60

#: GHSA's own severity words, which OSV carries under ``database_specific``. Ordered
#: worst-first so a package's headline severity is the worst advisory against it.
_SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "moderate", "low", "unknown")

_OFFLINE_NOTE = (
    "offline — OSV.dev could not be reached, so NO package has a vulnerability "
    "verdict. Every row is 'unknown'. This is not a clean bill of health."
)
_ONLINE_NOTE = (
    "Checked live against OSV.dev (the aggregator behind pip-audit: GHSA, PYSEC, NVD). "
    "'clean' means OSV answered and had no advisory for that exact version; 'unknown' "
    "means it could not be asked."
)
_PARTIAL_NOTE = (
    "Checked live against OSV.dev, but one or more batches could not be reached; those "
    "packages are 'unknown'. The remaining rows are real verdicts. NOT a complete "
    "clean bill of health."
)


class AdvisoryFeedUnreachableError(RuntimeError):
    """Raised when OSV cannot be reached at the network level.

    Distinct from OSV answering "no advisories", which is a verdict rather than a
    failure — collapsing the two is how an offline box reports itself secure.
    """


@dataclass(frozen=True)
class Vulnerability:
    """One advisory against one installed version.

    Attributes:
        id: The OSV id (``GHSA-…``, ``PYSEC-…``).
        aliases: Other identifiers for the same advisory — the ``CVE-…`` a reader
            recognises usually lives here rather than in ``id``.
        summary: One-line description, as OSV wrote it. Empty when the detail fetch
            was capped out.
        severity: ``critical`` / ``high`` / ``moderate`` / ``low`` / ``unknown``.
        detail_fetched: Whether the per-advisory detail call ran. ``False`` means the
            id is real but the summary and severity were not retrieved.
    """

    id: str
    aliases: tuple[str, ...] = ()
    summary: str = ""
    severity: str = "unknown"
    detail_fetched: bool = True

    def as_dict(self) -> dict[str, object]:
        """Return the advisory as a JSON-ready dict."""
        return {
            "id": self.id,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "severity": self.severity,
            "detail_fetched": self.detail_fetched,
        }


@dataclass(frozen=True)
class PackageAdvisories:
    """The verdict for one installed distribution.

    Attributes:
        name: Distribution name.
        version: The installed version that was queried.
        status: ``vulnerable`` when OSV returned at least one advisory, ``clean``
            when OSV answered and returned none, ``unknown`` when it could not be
            asked. Never ``clean`` without a real answer.
        vulnerabilities: The advisories OSV returned, worst severity first.
        note: Why the status is what it is, when that needs saying.
    """

    name: str
    version: str
    status: str
    vulnerabilities: tuple[Vulnerability, ...] = ()
    note: str = ""

    @property
    def worst_severity(self) -> str:
        """The severity of the worst advisory against this package."""
        for level in _SEVERITY_ORDER:
            if any(v.severity == level for v in self.vulnerabilities):
                return level
        return "none" if self.status == "clean" else "unknown"

    def as_dict(self) -> dict[str, object]:
        """Return the package verdict as a JSON-ready dict."""
        return {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "worst_severity": self.worst_severity,
            "note": self.note,
            "vulnerabilities": [v.as_dict() for v in self.vulnerabilities],
        }


@dataclass(frozen=True)
class AdvisoryAudit:
    """The whole audit: one row per package, plus the verdict on the run itself.

    Attributes:
        checked_at: ISO-8601 UTC time the audit ran.
        online: Whether OSV answered for at least one batch.
        note: How to read these results, in plain words.
        source: The advisory database queried.
        packages: One row per distribution audited, sorted worst-first.
        passed: ``True`` only when every package got a real answer **and** none of
            them is vulnerable. An audit that could not run does not pass.
    """

    checked_at: str
    online: bool
    note: str
    source: str
    packages: tuple[PackageAdvisories, ...] = ()

    @property
    def vulnerable(self) -> tuple[PackageAdvisories, ...]:
        """Packages with at least one advisory against the installed version."""
        return tuple(p for p in self.packages if p.status == "vulnerable")

    @property
    def unknown(self) -> tuple[PackageAdvisories, ...]:
        """Packages OSV could not be asked about."""
        return tuple(p for p in self.packages if p.status == "unknown")

    @property
    def passed(self) -> bool:
        """True only when every package answered and none carries an advisory."""
        return not self.vulnerable and not self.unknown and bool(self.packages)

    def severity_counts(self) -> dict[str, int]:
        """Return ``{severity: package count}`` across the vulnerable packages."""
        counts: dict[str, int] = {}
        for package in self.vulnerable:
            level = package.worst_severity
            counts[level] = counts.get(level, 0) + 1
        return counts

    def as_dict(self) -> dict[str, object]:
        """Return the whole audit as a JSON-ready dict."""
        return {
            "checked_at": self.checked_at,
            "online": self.online,
            "note": self.note,
            "source": self.source,
            "passed": self.passed,
            "packages_audited": len(self.packages),
            "packages_vulnerable": len(self.vulnerable),
            "packages_unknown": len(self.unknown),
            "severity_counts": self.severity_counts(),
            "packages": [p.as_dict() for p in self.packages],
        }

    def summary(self) -> str:
        """Return a one-screen human summary (the CLI/CI line)."""
        head = (
            f"OSV audit: {len(self.vulnerable)} vulnerable, {len(self.unknown)} unknown, "
            f"{len(self.packages)} audited — {'PASS' if self.passed else 'FAIL'}"
        )
        lines = [head, f"  source: {self.source} (online={self.online})"]
        for package in self.vulnerable:
            # Deduplicated: GHSA and PYSEC both alias the same CVE, so the raw list
            # prints one advisory twice and reads as two.
            seen: list[str] = []
            for vulnerability in package.vulnerabilities:
                label = vulnerability.aliases[0] if vulnerability.aliases else vulnerability.id
                if label not in seen:
                    seen.append(label)
            ids = ", ".join(seen[:4])
            lines.append(
                f"  {package.name} {package.version} [{package.worst_severity}]: {ids}"
            )
        return "\n".join(lines)


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    """POST ``payload`` as JSON and return the decoded body.

    Raises:
        AdvisoryFeedUnreachableError: On any network-level failure, or on a body that
            is not decodable JSON — both mean no verdict was obtained.
    """
    body = json.dumps(payload).encode("utf-8")
    try:
        import httpx

        try:
            response = httpx.post(
                url, content=body, headers={"Content-Type": "application/json"},
                timeout=_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise AdvisoryFeedUnreachableError(str(exc)) from exc
        if response.status_code != 200:
            raise AdvisoryFeedUnreachableError(f"OSV returned HTTP {response.status_code}")
        try:
            return dict(response.json())
        except ValueError as exc:
            raise AdvisoryFeedUnreachableError("OSV returned a non-JSON body") from exc
    except ImportError:
        pass  # fall through to urllib

    import urllib.error
    import urllib.request

    request = urllib.request.Request(  # noqa: S310 - a constant https URL
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
            return dict(json.loads(response.read()))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise AdvisoryFeedUnreachableError(str(exc)) from exc


def _get_json(url: str) -> dict[str, object] | None:
    """GET ``url`` and return the decoded body, or ``None`` on any failure.

    A failed *detail* fetch is not fatal: the advisory id is already known, so the
    row degrades to ``severity="unknown"`` rather than the audit degrading to offline.
    """
    try:
        import httpx

        response = httpx.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
        if response.status_code != 200:
            return None
        return dict(response.json())
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - a detail miss must not fail the audit
        logger.debug("OSV detail fetch failed for %s", url, exc_info=True)
        return None

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as response:  # noqa: S310
            return dict(json.loads(response.read()))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        logger.debug("OSV detail fetch failed for %s", url, exc_info=True)
        return None


def _severity_of(detail: dict[str, object]) -> str:
    """Return the advisory's severity word, lower-cased, or ``unknown``.

    Prefers GHSA's own qualitative rating (which OSV carries verbatim under
    ``database_specific.severity``) over parsing a CVSS vector string: a base score
    computed here would be a second opinion presented as the publisher's.
    """
    specific = detail.get("database_specific")
    if isinstance(specific, dict):
        level = specific.get("severity")
        if isinstance(level, str) and level.strip().lower() in _SEVERITY_ORDER:
            return level.strip().lower()
    return "unknown"


def _detail_for(vuln_id: str) -> Vulnerability:
    """Fetch one advisory's human-readable half; degrade to id-only on failure."""
    detail = _get_json(OSV_VULN_URL.format(vuln_id=vuln_id))
    if detail is None:
        return Vulnerability(
            id=vuln_id, severity="unknown", detail_fetched=False,
            summary="Advisory detail could not be fetched.",
        )
    aliases = detail.get("aliases")
    summary = detail.get("summary") or detail.get("details") or ""
    return Vulnerability(
        id=vuln_id,
        aliases=tuple(a for a in (aliases or []) if isinstance(a, str)),
        summary=str(summary).split("\n", 1)[0][:300],
        severity=_severity_of(detail),
        detail_fetched=True,
    )


def _severity_rank(vulnerability: Vulnerability) -> int:
    """Sort key: worst severity first, then by id for a stable order."""
    try:
        return _SEVERITY_ORDER.index(vulnerability.severity)
    except ValueError:
        return len(_SEVERITY_ORDER)


def audit_dependencies(
    packages: list[str] | None = None,
    *,
    components: list[Component] | None = None,
    detail_limit: int = _MAX_DETAIL_FETCHES,
) -> AdvisoryAudit:
    """Audit the installed distributions against OSV.dev.

    Args:
        packages: Optional subset of distribution names to audit (case-insensitive);
            ``None``/empty audits every installed distribution.
        components: Pre-resolved inventory, mainly for tests; resolved live from the
            interpreter when ``None``.
        detail_limit: Cap on per-advisory detail fetches. Advisories beyond it are
            still reported, by id, with ``severity="unknown"``.

    Returns:
        An :class:`AdvisoryAudit`. ``passed`` is ``True`` only when OSV answered for
        every package and none of them carries an advisory.
    """
    inventory = components if components is not None else resolve_inventory()
    if packages:
        wanted = {p.strip().lower() for p in packages if p and p.strip()}
        inventory = [c for c in inventory if c.name.lower() in wanted]

    rows: list[PackageAdvisories] = []
    unreachable_batches = 0
    total_batches = 0
    detail_cache: dict[str, Vulnerability] = {}
    fetched = 0

    for start in range(0, len(inventory), _BATCH_SIZE):
        chunk = inventory[start : start + _BATCH_SIZE]
        total_batches += 1
        queries = [
            {"package": {"name": c.name.lower(), "ecosystem": "PyPI"}, "version": c.version}
            for c in chunk
        ]
        try:
            body = _post_json(OSV_BATCH_URL, {"queries": queries})
        except AdvisoryFeedUnreachableError:
            logger.info("OSV unreachable for a batch of %d packages.", len(chunk))
            unreachable_batches += 1
            rows.extend(
                PackageAdvisories(
                    name=c.name,
                    version=c.version,
                    status="unknown",
                    note="advisory database unreachable for this batch",
                )
                for c in chunk
            )
            continue

        results = body.get("results")
        results = results if isinstance(results, list) else []
        for index, component in enumerate(chunk):
            entry = results[index] if index < len(results) else {}
            raw = entry.get("vulns") if isinstance(entry, dict) else None
            ids = [
                str(v["id"])
                for v in (raw or [])
                if isinstance(v, dict) and isinstance(v.get("id"), str)
            ]
            if not ids:
                rows.append(
                    PackageAdvisories(name=component.name, version=component.version,
                                      status="clean")
                )
                continue
            found: list[Vulnerability] = []
            for vuln_id in ids:
                if vuln_id in detail_cache:
                    found.append(detail_cache[vuln_id])
                    continue
                if fetched >= detail_limit:
                    found.append(
                        Vulnerability(
                            id=vuln_id, severity="unknown", detail_fetched=False,
                            summary=(
                                "Advisory detail not fetched (per-audit detail cap "
                                f"of {detail_limit} reached)."
                            ),
                        )
                    )
                    continue
                fetched += 1
                detail = _detail_for(vuln_id)
                detail_cache[vuln_id] = detail
                found.append(detail)
            rows.append(
                PackageAdvisories(
                    name=component.name,
                    version=component.version,
                    status="vulnerable",
                    vulnerabilities=tuple(sorted(found, key=_severity_rank)),
                )
            )

    online = total_batches > 0 and unreachable_batches < total_batches
    if not online:
        note = _OFFLINE_NOTE
    elif unreachable_batches:
        note = _PARTIAL_NOTE
    else:
        note = _ONLINE_NOTE

    order = {"vulnerable": 0, "unknown": 1, "clean": 2}
    rows.sort(key=lambda p: (order.get(p.status, 3), _SEVERITY_ORDER.index(p.worst_severity)
                             if p.worst_severity in _SEVERITY_ORDER else 9, p.name.lower()))
    return AdvisoryAudit(
        checked_at=datetime.now(UTC).isoformat(),
        online=online,
        note=note,
        source=OSV_BATCH_URL,
        packages=tuple(rows),
    )


#: Where a decision about an advisory that cannot yet be fixed is written down.
ACKNOWLEDGED_PATH = "backend/known_advisories.json"


def acknowledged_ids(path: str = ACKNOWLEDGED_PATH) -> frozenset[str]:
    """Return the advisory ids this repository has recorded a decision about.

    An acknowledgement is a statement about **the build**, never about the risk: it
    stops :func:`main` failing CI and does nothing else. ``POST /stack/advisories``
    still reports the package as ``vulnerable``, :attr:`AdvisoryAudit.passed` is still
    ``False``, and the compliance surface still counts it. What it buys is that every
    advisory *not* listed there fails the build — which is only a real property if the
    listed ones stop keeping it permanently red for a reason nobody on the branch can
    act on.

    Args:
        path: Repository-relative path to the acknowledgement file.

    Returns:
        Every acknowledged advisory id. Empty when the file is missing or unreadable,
        which fails **closed**: an unreadable acknowledgement list means nothing is
        acknowledged, not that everything is.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    try:
        data = json.loads((root / path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Acknowledgement file %s not readable; acknowledging nothing.", path)
        return frozenset()
    ids: set[str] = set()
    for entry in data.get("acknowledged", []):
        if isinstance(entry, dict):
            ids.update(str(i) for i in entry.get("ids", []) if isinstance(i, str))
    return frozenset(ids)


def main() -> int:
    """Run the audit and print the summary — the CI gate's entry point.

    Fails on any advisory the repository has **not** recorded a decision about (see
    :func:`acknowledged_ids`), and on any package OSV could not be asked about: a gate
    that goes green because it could not run is the failure mode this whole module
    exists to refuse.

    Returns:
        ``0`` when nothing unacknowledged was found and every package answered, ``1``
        otherwise.
    """
    audit = audit_dependencies()
    print(audit.summary())  # noqa: T201 - this is a CLI
    known = acknowledged_ids()
    unacknowledged = [
        (package, vulnerability)
        for package in audit.vulnerable
        for vulnerability in package.vulnerabilities
        if vulnerability.id not in known
    ]
    if unacknowledged:
        print("\nUNACKNOWLEDGED advisories — this is what fails the build:")  # noqa: T201
        for package, vulnerability in unacknowledged:
            alias = vulnerability.aliases[0] if vulnerability.aliases else vulnerability.id
            print(  # noqa: T201
                f"  {package.name} {package.version}: {vulnerability.id} ({alias}) "
                f"[{vulnerability.severity}] {vulnerability.summary}"
            )
        print(  # noqa: T201
            f"\nFix them, or record the decision in {ACKNOWLEDGED_PATH} with what pins "
            "the vulnerable version and what would release it."
        )
        return 1
    if audit.unknown:
        print(  # noqa: T201
            f"\n{len(audit.unknown)} package(s) got no answer from the advisory "
            "database. An audit that could not run is not an audit that found nothing."
        )
        return 1
    acknowledged = sum(len(p.vulnerabilities) for p in audit.vulnerable)
    if acknowledged:
        print(  # noqa: T201
            f"\n{acknowledged} advisory/advisories are recorded in "
            f"{ACKNOWLEDGED_PATH}; the build is not blocked on them. They are still "
            "reported as vulnerable on POST /stack/advisories."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
