"""Live dependency freshness check for ``POST /stack/patch-check``.

For each tracked (or requested) package this resolves the **installed** version via
:func:`importlib.metadata.version` and the **latest** version via a live query to the
PyPI JSON API (``https://pypi.org/pypi/{name}/json``, short timeout, best-effort).

**Honesty rule — no clean bill of health without a real answer.** A package is only
ever marked ``current`` after the registry actually returned a version to compare
against.

**Per-package best-effort.** Each package is queried independently, so one package's
network failure or timeout no longer discards the packages already resolved. A package
the registry answered for gets its real ``current``/``outdated`` verdict; a package that
times out or fails at the network level gets ``unknown`` with an honest per-row note.
``online`` reflects whether the registry was reachable for **at least one** package
(``True`` if any package got a real answer, ``False`` only when *none* did). When every
package is unreachable the check degrades to ``online=False`` with a note that says
plainly this is *not* a clean bill of health; the last fully-successful result is cached
in-process and returned then (with its real ``checked_at``) if we have one, else the
honest unknown set.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib import metadata

from app.api.schemas import PatchCheckResponse, PatchResult

logger = logging.getLogger(__name__)

# PyPI JSON endpoint template and the per-request network budget.
_PYPI_URL = "https://pypi.org/pypi/{name}/json"
_TIMEOUT_S = 3.0

# The default tracked stack for the freshness check — the backend distributions
# whose pins matter most for security/patch posture. Requests may narrow this.
_TRACKED: tuple[str, ...] = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "langgraph",
    "litellm",
    "sqlalchemy",
    "xgboost",
    "mapie",
    "shap",
    "scikit-learn",
    "redis",
    "neo4j",
    "nemoguardrails",
    "arize-phoenix",
    "opentelemetry-sdk",
    "mcp",
    "httpx",
)

_OFFLINE_NOTE = (
    "offline — could not verify against the registry; NOT a clean bill of health. "
    "Every package is reported 'unknown' because no live version was available to "
    "compare against."
)
_ONLINE_NOTE = (
    "Checked live against the PyPI JSON registry. 'current' means installed == latest; "
    "'outdated' means a newer release exists; 'unknown' means the package is not "
    "installed or the registry had no version for it."
)
_PARTIAL_NOTE = (
    "Checked live against the PyPI JSON registry, but one or more packages could not be "
    "reached (marked 'unknown' — registry unreachable for this package); the remaining "
    "rows are real installed-vs-latest verdicts. NOT a complete clean bill of health."
)

# Last successful (online) result, cached in-process with its real timestamp.
_LAST_SUCCESS: PatchCheckResponse | None = None


class RegistryUnreachableError(RuntimeError):
    """Raised when the package registry cannot be reached at the network level.

    Distinct from a *valid* registry answer of "no such package" (which is not a
    network failure) — this signals the whole check must degrade to offline.
    """


def tracked_packages() -> tuple[str, ...]:
    """Return the default tracked package set (used when the request names none)."""
    return _TRACKED


def reset_cache() -> None:
    """Clear the in-process last-successful cache (for deterministic tests)."""
    global _LAST_SUCCESS
    _LAST_SUCCESS = None


def _installed_version(name: str) -> str | None:
    """Return the installed version of ``name``, or ``None`` if not installed."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken dist must not abort the whole check
        logger.debug("Installed-version lookup failed for %s", name, exc_info=True)
        return None


def _parse_latest(payload: bytes | str) -> str | None:
    """Extract the latest version from a PyPI JSON payload (``info.version``)."""
    try:
        data = json.loads(payload)
    except Exception:  # noqa: BLE001 - a malformed body is a non-answer, not a crash
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version else None


def _fetch_latest(name: str) -> str | None:
    """Query PyPI for ``name``'s latest version.

    Returns the latest version string, or ``None`` when the registry answered but had
    no usable version (e.g. a 404 for an unknown/renamed package). Raises
    :class:`RegistryUnreachableError` on any *network-level* failure (offline, timeout,
    DNS) so the caller can degrade the whole check to offline.

    Uses ``httpx`` when available, else falls back to the stdlib ``urllib``.
    """
    url = _PYPI_URL.format(name=name)
    try:
        import httpx

        try:
            resp = httpx.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:  # connect/timeout/transport → unreachable
            raise RegistryUnreachableError(str(exc)) from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            # A non-200/404 is an unusable answer for this package, but the registry
            # itself was reachable — treat as "unknown", not offline.
            return None
        return _parse_latest(resp.content)
    except ImportError:
        pass  # fall through to urllib

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            return _parse_latest(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RegistryUnreachableError(str(exc)) from exc


def _status(installed: str | None, latest: str | None) -> str:
    """Compare installed vs latest → 'current' | 'outdated' | 'unknown'.

    Never returns 'current' without both a real installed and a real latest version.
    """
    if installed is None or latest is None:
        return "unknown"
    try:
        from packaging.version import Version

        return "current" if Version(installed) >= Version(latest) else "outdated"
    except Exception:  # noqa: BLE001 - unparseable versions → honest 'unknown'
        if installed == latest:
            return "current"
        return "unknown"


def _offline_result(names: list[str]) -> PatchCheckResponse:
    """Return the honest offline set: every package 'unknown', ``online=False``.

    Prefers the cached last-successful result (with its real ``checked_at``) when one
    exists and covers the requested packages — the honest "here's what we last knew,
    and we could not re-verify" answer.
    """
    global _LAST_SUCCESS
    if _LAST_SUCCESS is not None:
        cached_names = {r.name for r in _LAST_SUCCESS.results}
        if set(names).issubset(cached_names):
            wanted = set(names)
            return PatchCheckResponse(
                checked_at=_LAST_SUCCESS.checked_at,
                online=False,
                note=(
                    "offline — could not re-verify against the registry; showing the "
                    "last successful check (see checked_at). NOT a fresh clean bill of "
                    "health."
                ),
                results=[r for r in _LAST_SUCCESS.results if r.name in wanted],
            )
    return PatchCheckResponse(
        checked_at=datetime.now(UTC).isoformat(),
        online=False,
        note=_OFFLINE_NOTE,
        results=[
            PatchResult(
                name=name,
                installed=_installed_version(name),
                latest=None,
                status="unknown",
                note="registry unreachable",
            )
            for name in names
        ],
    )


def patch_check(packages: list[str] | None = None) -> PatchCheckResponse:
    """Check installed vs latest for the tracked (or requested) packages.

    Args:
        packages: Optional subset of package names to check; ``None``/empty checks the
            full tracked stack.

    Returns:
        A :class:`PatchCheckResponse`. Each package is resolved independently:
        reachable packages get real ``current``/``outdated``/``unknown`` verdicts; a
        package that fails at the network level gets ``unknown`` with an honest per-row
        note. ``online`` is ``True`` when at least one package got a real registry
        answer. A fully-successful check (every package reachable) is cached in-process;
        when *no* package is reachable the check degrades to ``online=False`` (or the
        cached last-successful result).
    """
    global _LAST_SUCCESS
    names = [n for n in (packages or []) if n] or list(_TRACKED)

    results: list[PatchResult] = []
    unreachable = 0
    for name in names:
        installed = _installed_version(name)
        try:
            # Per-package, best-effort: this package's own network budget (``_TIMEOUT_S``).
            latest = _fetch_latest(name)  # may raise RegistryUnreachableError
        except RegistryUnreachableError:
            # Only THIS package failed at the network level — mark it unknown honestly
            # and keep going; a resolved neighbour must not be discarded.
            logger.debug("PyPI unreachable for %s during patch-check.", name, exc_info=True)
            unreachable += 1
            results.append(
                PatchResult(
                    name=name,
                    installed=installed,
                    latest=None,
                    status="unknown",
                    note="registry unreachable for this package",
                )
            )
            continue
        # Registry answered for this package (``latest`` may be a real version or ``None``
        # when it had no usable version, e.g. a 404 — reachable either way).
        status = _status(installed, latest)
        note = None
        if status == "unknown":
            note = "not installed" if installed is None else "no version on registry"
        results.append(
            PatchResult(
                name=name,
                installed=installed,
                latest=latest,
                status=status,  # type: ignore[arg-type]
                note=note,
            )
        )

    reachable = len(names) - unreachable
    if reachable == 0:
        # No package got a real registry answer — honestly offline (with cache fallback).
        logger.info("PyPI registry unreachable for every package; degrading to offline.")
        return _offline_result(names)

    response = PatchCheckResponse(
        checked_at=datetime.now(UTC).isoformat(),
        online=True,
        note=_ONLINE_NOTE if unreachable == 0 else _PARTIAL_NOTE,
        results=results,
    )
    # Only a fully-successful check (every package reachable) becomes the cached
    # "last successful" baseline the offline path falls back to.
    if unreachable == 0:
        _LAST_SUCCESS = response
    return response
