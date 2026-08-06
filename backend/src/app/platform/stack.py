"""Live software bill-of-materials for ``GET /stack``.

Every backend row's version is resolved at request time from the **actually
installed** distribution via :func:`importlib.metadata.version` — so an
optional-group dependency that isn't installed honestly reports ``version=None``
rather than a made-up pin. Each component is mapped to the branded Aegis module it
powers, resolved from :data:`app.capabilities.AEGIS_MODULES` (the single source of
truth) so the branded names never drift from the manifest.

The small frontend set is parsed from ``frontend/package.json`` at request time
(best-effort; ``version=None`` on any read/parse failure), keeping the console's own
stack honest without hand-maintaining a second copy.
"""

from __future__ import annotations

import json
import logging
import platform as _platform
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from app.api.schemas import StackComponent, StackResponse
from app.capabilities import AEGIS_MODULES

logger = logging.getLogger(__name__)

# Branded Aegis module name by manifest key — resolved from the manifest so the
# stack view can never quote a module name the product doesn't actually ship.
_MODULE_NAME: dict[str, str] = {m.key: m.name for m in AEGIS_MODULES}


def _module(key: str | None) -> str | None:
    """Return the branded Aegis module name for a manifest ``key`` (or ``None``)."""
    return _MODULE_NAME.get(key) if key else None


# The tracked backend stack: (label, category, distribution, aegis-module-key).
# ``category`` is the coarse layer the console groups by; the module key ties the
# row to the branded Aegis module it powers (``None`` = shared infra / transport).
_BACKEND_STACK: list[tuple[str, str, str, str | None]] = [
    ("FastAPI", "backend", "fastapi", None),
    ("Uvicorn", "backend", "uvicorn", None),
    ("Pydantic", "backend", "pydantic", None),
    ("SSE-Starlette", "backend", "sse-starlette", None),
    ("LiteLLM", "backend", "litellm", "gateway"),
    ("LangGraph", "backend", "langgraph", "router"),
    ("SQLAlchemy", "backend", "sqlalchemy", "memory"),
    ("XGBoost", "backend", "xgboost", "signal"),
    ("MAPIE (conformal)", "backend", "mapie", "signal"),
    ("SHAP", "backend", "shap", "signal"),
    ("scikit-learn", "backend", "scikit-learn", "signal"),
    ("NeMo Guardrails", "backend", "nemoguardrails", "guardrails"),
    ("OpenTelemetry SDK", "backend", "opentelemetry-sdk", "trace"),
    ("Arize Phoenix", "backend", "arize-phoenix", "trace"),
    ("MCP SDK", "backend", "mcp", "mcp"),
    # Infra clients — the datastore drivers behind the knowledge modules.
    ("Redis", "infra", "redis", "cache"),
    ("Neo4j driver", "infra", "neo4j", "retrieval"),
    ("LightRAG", "infra", "lightrag-hku", "retrieval"),
    ("pgvector", "infra", "pgvector", "memory"),
]

# The frontend set read from ``frontend/package.json``: (label, npm package, module).
_FRONTEND_SET: list[tuple[str, str, str | None]] = [
    ("React", "react", "Console"),
    ("Vite", "vite", "Console"),
    ("TypeScript", "typescript", "Console"),
    ("Recharts", "recharts", "Console"),
    ("Tailwind CSS", "tailwindcss", "Console"),
]


def _installed_version(dist: str) -> str | None:
    """Return the installed version of ``dist``, or ``None`` if not installed.

    ``None`` is honest: an optional-group dependency that was never installed has no
    version, and we never invent one.
    """
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken dist must not break the inventory
        logger.debug("Version lookup failed for %s", dist, exc_info=True)
        return None


def _find_frontend_package_json() -> Path | None:
    """Best-effort locate ``frontend/package.json`` walking up from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "frontend" / "package.json"
        if candidate.is_file():
            return candidate
    return None


def _frontend_versions() -> dict[str, str | None]:
    """Parse pinned frontend dependency versions (best-effort; empty on failure).

    Strips the leading range operator (``^``/``~``/``>=`` …) so the console shows the
    pinned baseline. Any read/parse failure yields an empty map, and each component
    then reports ``version=None`` — honest rather than guessed.
    """
    path = _find_frontend_package_json()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing/broken lockfile just means no versions
        logger.debug("Could not read frontend package.json at %s", path, exc_info=True)
        return {}
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies"):
        block = data.get(section)
        if isinstance(block, dict):
            deps.update({k: v for k, v in block.items() if isinstance(v, str)})
    cleaned: dict[str, str | None] = {}
    for name, spec in deps.items():
        cleaned[name] = spec.lstrip("^~>=< ").strip() or None
    return cleaned


def build_stack() -> StackResponse:
    """Inventory the live stack: runtime + backend + frontend + infra components."""
    components: list[StackComponent] = [
        StackComponent(
            name="Python",
            category="runtime",
            package="python",
            version=_platform.python_version(),
            aegis_module=None,
        )
    ]

    for label, category, dist, module_key in _BACKEND_STACK:
        components.append(
            StackComponent(
                name=label,
                category=category,  # type: ignore[arg-type]
                package=dist,
                version=_installed_version(dist),
                aegis_module=_module(module_key),
            )
        )

    frontend = _frontend_versions()
    for label, npm_name, module in _FRONTEND_SET:
        components.append(
            StackComponent(
                name=label,
                category="frontend",
                package=npm_name,
                version=frontend.get(npm_name),
                aegis_module=module,
            )
        )

    return StackResponse(
        generated_at=datetime.now(UTC).isoformat(),
        components=components,
    )
