"""Route coverage — every non-public endpoint must be reachable from a portal.

Why this exists (§3.10): the backend and the browser drifted apart silently and
repeatedly. ``POST /query`` has always admitted every authenticated role, yet
``ROLE_SECTIONS.client`` carried no ``console`` entry, so the role the product
exists for could not ask a question. Nothing failed — there was no test that
related the served route table to the portal catalogue, so the two could disagree
forever.

This module relates them, from the real artefacts on both sides:

* the **served** route table, read from the live ``app.api.routes.router`` (not a
  hand-written list, which would drift the same way);
* the **portal catalogue** ``ROLE_SECTIONS`` / ``SECTIONS`` in
  ``web/src/lib/portal.ts``;
* the **section renderer** ``web/src/app/app/[role]/[section]/page.tsx``, which
  decides — per portal — which component a section actually mounts (the ``[role]``
  URL segment carries the *fine* role since §7.2, so there are five portals, not
  four, and the two admin tiers no longer share one);
* the **call graph** from each mounted component through its imports to the
  ``web/src/lib/api`` functions that name endpoint paths and methods.

The last piece is a static analysis of TypeScript, so it is deliberately narrow
and its one assumption is itself asserted by
:func:`test_every_backend_call_goes_through_the_api_layer`: **no module outside
``web/src/lib/api/`` calls ``fetch``**. While that holds, an endpoint is reachable
from a portal if and only if some mounted component transitively imports the api
function that names it. When it stops holding the assertion fails, rather than
this file quietly under-counting.

Two things are asserted:

1. Every non-public route is reachable from at least one portal, or carries an
   explicit entry in :data:`UNREACHABLE_BY_DESIGN` naming the reason and the phase
   that owns it. An allowlist entry is a recorded decision:
   :func:`test_allowlist_is_neither_stale_nor_wrong` fails if an entry names a
   route that no longer exists **or** one that has since been wired up, so wiring
   a surface forces the entry out.
2. Every role reaches every section it is supposed to have — the section is in the
   role's catalogue, is defined in ``SECTIONS``, and the renderer really mounts a
   component for that (role, section) pair rather than falling through to 404.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.params import Depends as DependsParam

from app.api.routes import router

# ─────────────────────────────────────────────────────────────────────────────
# Repository layout
# ─────────────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_SRC = _REPO_ROOT / "web" / "src"
_API_DIR = _WEB_SRC / "lib" / "api"
_PORTAL_TS = _WEB_SRC / "lib" / "portal.ts"
_SECTION_PAGE = _WEB_SRC / "app" / "app" / "[role]" / "[section]" / "page.tsx"


# ─────────────────────────────────────────────────────────────────────────────
# The served route table
# ─────────────────────────────────────────────────────────────────────────────

#: Guards that make a route non-public. A route whose endpoint signature depends on
#: none of these serves an unauthenticated caller and is out of scope here: the
#: browser needs no portal to reach it (the landing page calls several directly).
_AUTH_GUARDS = frozenset(
    {
        "require_auth",
        "require_admin",
        "require_devops",
        "require_ai_team",
        "require_client",
        "require_platform_admin",
        "require_tenant_admin",
        # The red-team control plane's two guards (§7.13). Both are built from
        # ``require_auth`` and refuse a role outright; without them listed here their
        # routes would be classified **public** and silently dropped from this whole
        # analysis — which is the opposite of what a guard named ``require_*`` means.
        "require_redteam_operator",
        "require_redteam_reader",
        # ``require_roles(...)`` returns a closure named ``_dep``; it is only ever
        # built from ``require_auth``, so its presence marks an authenticated route.
        "_dep",
    }
)


@dataclass(frozen=True)
class Endpoint:
    """One served HTTP endpoint: an upper-case method and a normalised path.

    Path parameters are normalised to a bare ``{}`` on both sides of the
    comparison, so ``/memory/sessions/{session_id}/messages`` (FastAPI) and
    ``/memory/sessions/${encodeURIComponent(id)}/messages`` (TypeScript) are the
    same endpoint.
    """

    method: str
    path: str

    def __str__(self) -> str:  # pragma: no cover - assertion message formatting
        return f"{self.method} {self.path}"


def _normalise_route_path(path: str) -> str:
    """Collapse FastAPI path parameters to ``{}`` so both sides compare equal."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _served_endpoints() -> tuple[set[Endpoint], set[Endpoint]]:
    """Return ``(non_public, public)`` endpoints from the live router.

    Read from the router itself rather than a literal list: a hand-maintained copy
    would drift out of date exactly like the portal catalogue it is here to police.
    """
    import inspect

    non_public: set[Endpoint] = set()
    public: set[Endpoint] = set()
    for route in router.routes:
        methods = getattr(route, "methods", None)
        if methods is None:  # a websocket route carries no methods
            continue
        guards = {
            default.dependency.__name__
            for default in (
                param.default
                for param in inspect.signature(route.endpoint).parameters.values()
            )
            if isinstance(default, DependsParam) and default.dependency is not None
        }
        target = non_public if guards & _AUTH_GUARDS else public
        for method in methods - {"HEAD", "OPTIONS"}:
            target.add(Endpoint(method, _normalise_route_path(route.path)))
    return non_public, public


# ─────────────────────────────────────────────────────────────────────────────
# TypeScript reading — string literals, endpoint paths, imports
# ─────────────────────────────────────────────────────────────────────────────

#: Placeholder standing in for one ``${...}`` template interpolation.
_INTERP = "\x00"


def _string_literals(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, value)`` for every string literal, skipping comments.

    Comments must be skipped or the analysis lies in the most damaging direction:
    ``RolesAccess.tsx`` documents ``POST /admin/users/{id}/role`` in a docstring, and
    a naive scan would count the prose as a call site. Template interpolations are
    replaced by :data:`_INTERP` so ``/memory/sessions/${id}/messages`` keeps its
    shape without keeping the expression.
    """
    out: list[tuple[int, int, str]] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and text[i + 1 : i + 2] == "/":
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            continue
        if char == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if char not in "\"'`":
            i += 1
            continue
        delim, j, buf = char, i + 1, []
        while j < n:
            here = text[j]
            if here == "\\":
                j += 2
                continue
            if here == delim:
                break
            if delim == "`" and here == "$" and text[j + 1 : j + 2] == "{":
                depth, k = 0, j + 1
                while k < n:
                    if text[k] == "{":
                        depth += 1
                    elif text[k] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                buf.append(_INTERP)
                j = k + 1
                continue
            buf.append(here)
            j += 1
        out.append((i, j + 1, "".join(buf)))
        i = j + 1
    return out


def _as_endpoint_path(literal: str) -> str | None:
    """Normalise one string literal into an endpoint path, or ``None``.

    Handles the three shapes the api layer actually writes:
    ``'/graph'``, ``` `${API_BASE}/query` ``` (a leading interpolation) and
    ``` `/admin/users${q}` ``` (a trailing query-string interpolation, which is not
    a path segment). A segment that is *only* an interpolation is a path parameter.
    """
    raw = literal.lstrip(_INTERP)
    if not raw.startswith("/"):
        return None
    raw = raw.split("?", 1)[0]
    segments = [
        "{}" if seg == _INTERP else seg.replace(_INTERP, "") for seg in raw.split("/")
    ]
    while len(segments) > 1 and segments[-1] == "":
        segments.pop()
    return "/".join(segments)


def _brace_body(text: str, start: int) -> tuple[int, int]:
    """Return the ``(open, close)`` offsets of the brace body following ``start``."""
    depth, i = 0, start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    open_brace = text.index("{", i)
    depth, j = 0, open_brace
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return open_brace, j


_METHOD_RE = re.compile(r"method:\s*['\"](\w+)['\"]")


def _api_endpoints_by_export() -> dict[str, frozenset[Endpoint]]:
    """Map each exported ``web/src/lib/api`` function to the endpoints it calls.

    Attribution is per function, not per file: every component importing one symbol
    from ``client.ts`` would otherwise inherit all fifty endpoints that file names.
    """
    out: dict[str, frozenset[Endpoint]] = {}
    for module in sorted(_API_DIR.glob("*.ts")):
        text = module.read_text()
        literals = _string_literals(text)
        for match in re.finditer(r"export\s+(?:async\s+)?function\s+(\w+)", text):
            open_brace, close_brace = _brace_body(text, match.end())
            body = text[open_brace : close_brace + 1]
            paths = {
                path
                for (start, _end, value) in literals
                if open_brace <= start <= close_brace
                and (path := _as_endpoint_path(value)) is not None
            }
            if not paths:
                continue
            methods = set(_METHOD_RE.findall(body)) or {"GET"}
            out[match.group(1)] = frozenset(
                Endpoint(method.upper(), path) for path in paths for method in methods
            )
    return out


_IMPORT_RE = re.compile(
    r"import\s+(?:type\s+)?(?:\{([^}]*)\}|(\w+))?[^'\"]*from\s+['\"]([^'\"]+)['\"]",
    re.S,
)
_DYNAMIC_IMPORT_RE = re.compile(r"import\(\s*['\"]([^'\"]+)['\"]")


def _resolve_import(spec: str, importer: Path) -> Path | None:
    """Resolve a ``@/``-aliased or relative module specifier to a file on disk."""
    if spec.startswith("@/"):
        base = _WEB_SRC / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        return None  # a package import: never a source module of ours
    for candidate in (
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base / "index.ts",
        base / "index.tsx",
    ):
        if candidate.is_file():
            return candidate
    return None


def _module_edges(module: Path) -> tuple[set[str], set[Path]]:
    """Return the ``(imported names, imported modules)`` of one source module.

    Both static and ``import('…')`` dynamic imports count — ``ConsoleMount`` reaches
    the console (and therefore ``POST /query``) only through a dynamic import, so
    ignoring those would report the console as calling nothing.
    """
    text = module.read_text()
    names: set[str] = set()
    deps: set[Path] = set()
    for match in _DYNAMIC_IMPORT_RE.finditer(text):
        if (target := _resolve_import(match.group(1), module)) is not None:
            deps.add(target)
    for match in _IMPORT_RE.finditer(text):
        named, default_name, spec = match.group(1), match.group(2), match.group(3)
        if (target := _resolve_import(spec, module)) is not None:
            deps.add(target)
        for chunk in (named or "").split(","):
            name = chunk.strip().split(" as ")[0].strip().removeprefix("type ").strip()
            if name:
                names.add(name)
        if default_name:
            names.add(default_name)
    return names, deps


def _endpoints_reachable_from(entry: Path, api: dict[str, frozenset[Endpoint]]) -> set[Endpoint]:
    """Return every endpoint reachable from ``entry`` through its import graph.

    An imported name counts as used: ``next lint`` fails the build on an unused
    import, so "imported" and "called" are the same set in this codebase.
    """
    seen: set[Path] = set()
    stack = [entry]
    found: set[Endpoint] = set()
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        names, deps = _module_edges(module)
        for name in names:
            found |= api.get(name, frozenset())
        stack.extend(deps)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# The portal catalogue and the section renderer
# ─────────────────────────────────────────────────────────────────────────────


def _role_sections() -> dict[str, list[str]]:
    """Parse ``ROLE_SECTIONS`` — role → the section ids its portal exposes."""
    text = _PORTAL_TS.read_text()
    block = re.search(
        r"ROLE_SECTIONS:\s*Record<(?:Role|Portal), string\[\]>\s*=\s*\{(.*?)\n\}",
        text,
        re.S,
    )
    assert block is not None, f"ROLE_SECTIONS not found in {_PORTAL_TS}"
    return {
        match.group(1): re.findall(r"'([^']+)'", match.group(2))
        for match in re.finditer(r"(\w+):\s*\[(.*?)\]", block.group(1), re.S)
    }


def _section_ids() -> set[str]:
    """Parse the ids defined in the ``SECTIONS`` catalogue."""
    text = _PORTAL_TS.read_text()
    block = re.search(
        r"SECTIONS:\s*Record<string, Section>\s*=\s*\{(.*?)\n\}\n", text, re.S
    )
    assert block is not None, f"SECTIONS not found in {_PORTAL_TS}"
    return set(re.findall(r"^  (\w+):\s*\{", block.group(1), re.M))


@dataclass(frozen=True)
class _RenderBranch:
    """One ``if (…) return <Component …>`` branch of the section renderer."""

    section: str | None
    role: str | None
    component: str
    console_only: bool


_BRANCH_RE = re.compile(r"if \(([^)]*)\) return <(\w+)")

#: How the renderer names the portal it is switching on. It was `role` while the URL
#: segment carried the coarse role and is `portal` since §7.2 split the two admin
#: tiers; both are accepted so the parse does not depend on the variable's name.
_PORTAL_IN_CONDITION = re.compile(r"\b(?:portal|role) === '(\w+)'")


def _render_branches() -> list[_RenderBranch]:
    """Parse the section renderer's branches, in source (first-match) order."""
    text = _SECTION_PAGE.read_text()
    branches: list[_RenderBranch] = []
    for match in _BRANCH_RE.finditer(text):
        condition, component = match.group(1), match.group(2)
        section = re.search(r"section === '(\w+)'", condition)
        role = _PORTAL_IN_CONDITION.search(condition)
        branches.append(
            _RenderBranch(
                section=section.group(1) if section else None,
                role=role.group(1) if role else None,
                component=component,
                console_only="def.console" in condition,
            )
        )
    assert branches, f"no render branches parsed from {_SECTION_PAGE}"
    return branches


def _page_component_modules() -> dict[str, Path]:
    """Map each component name imported by the section renderer to its module."""
    names_to_module: dict[str, Path] = {}
    text = _SECTION_PAGE.read_text()
    for match in _IMPORT_RE.finditer(text):
        named, _default, spec = match.group(1), match.group(2), match.group(3)
        target = _resolve_import(spec, _SECTION_PAGE)
        if target is None:
            continue
        for chunk in (named or "").split(","):
            name = chunk.strip().split(" as ")[0].strip()
            if name:
                names_to_module[name] = target
    return names_to_module


def _component_for(role: str, section: str, console_sections: set[str]) -> str | None:
    """Return the component the renderer mounts for ``(role, section)``, or ``None``.

    First-match semantics, because every branch is a ``return``: the admin portal's
    ``dashboard`` mounts the command center and every other portal's mounts the
    metrics dashboard, and conflating the two would credit ``devops`` with the
    admin-only reads.
    """
    for branch in _render_branches():
        if branch.console_only:
            if section in console_sections:
                return branch.component
            continue
        if branch.section is not None and branch.section != section:
            continue
        if branch.role is not None and branch.role != role:
            continue
        if branch.section is None:
            continue
        return branch.component
    return None


def _console_sections() -> set[str]:
    """Return the section ids flagged ``console: true`` in the catalogue."""
    text = _PORTAL_TS.read_text()
    return {
        match.group(1)
        for match in re.finditer(
            r"^  (\w+): \{(?:(?!^  \w+: \{).)*?console: true", text, re.S | re.M
        )
    }


# ─────────────────────────────────────────────────────────────────────────────
# Decisions
# ─────────────────────────────────────────────────────────────────────────────

#: Sections each role must be able to reach. Not a mirror of ``ROLE_SECTIONS`` —
#: that would assert nothing — but the independently stated minimum, so deleting a
#: catalogue entry fails here instead of silently removing a surface.
#:
#: ``console`` on ``client`` is the §3.10 entry: the client is the role the product
#: exists for, and the console is the only surface that asks a question.
REQUIRED_SECTIONS: dict[str, frozenset[str]] = {
    "platform_admin": frozenset(
        {"dashboard", "governance", "audit", "roles", "forecast", "approvals"}
    ),
    # The portal that did not exist before §7.2: a tenant's own administrator was
    # borrowing the platform operator's. `approvals` is the one that makes it a portal
    # rather than a copy — deciding its tenant's gates is the authority §7.1 moved
    # here, and this row is what stops it being moved back out.
    "tenant_admin": frozenset({"dashboard", "approvals", "governance", "audit"}),
    "ai_team": frozenset({"console", "harness", "evals", "memory", "rag", "guardrails"}),
    "devops": frozenset({"stack", "patch", "security", "redteam", "latency"}),
    # `approvals` on the client is read-only and is still required: a user whose run
    # trips the HIGH-risk gate had no screen at all that told them what happened to it.
    "client": frozenset(
        {"console", "dashboard", "savings", "forecast", "risk", "approvals"}
    ),
}

#: Non-public endpoints deliberately not reachable from any portal, each with the
#: reason and the phase that owns wiring it. Adding a line here is a decision on
#: record, not a way to get to green: :func:`test_allowlist_is_neither_stale_nor_wrong`
#: fails if an entry names a route that no longer exists or one that has since been
#: wired, so the list cannot rot in either direction.
UNREACHABLE_BY_DESIGN: dict[tuple[str, str], str] = {
    # ── The usage rollup, superseded by the aggregate ────────────────────────
    # `GET /governance/dashboard` returns tenants + budgets + users + the usage
    # rollup + the audit tail in one tenant-scoped call, and is what the Governance
    # section renders. This remains the per-resource API read (used by integration
    # tests and by any API consumer); a portal calling it would issue a second round
    # trip for data it already has.
    #
    # The five provisioning entries that sat here — POST/GET `/admin/tenants`,
    # POST/GET `/admin/budgets`, POST `/admin/users` — are gone because phase 7.3
    # wired the forms (commit `8cbf446`), and the reads came with them. The allowlist
    # cannot rot in that direction: leaving them would fail
    # :func:`test_allowlist_is_neither_stale_nor_wrong` as loudly as omitting a real
    # one fails the reachability test.
    ("GET", "/admin/usage"): "superseded in the UI by the GET /governance/dashboard aggregate",
    # ── Right-to-erasure writes (phase 7 §6 owns the memory control plane) ───
    # The only hard deletes in the product. Exposing them from a read-only
    # inspector without a confirmation flow and an audit-visible trail would be
    # worse than leaving them API-only; the memory control plane in phase 7 is
    # where they get a screen.
    ("POST", "/memory/forget"): "phase 7 §6 — GDPR erasure needs a confirmation flow, not a button",
    (
        "DELETE",
        "/memory/facts/{}",
    ): "phase 7 §6 — GDPR erasure needs a confirmation flow, not a button",
    # ── The raw ledger-spend projection ──────────────────────────────────────
    # `ForecastView` deliberately serves the *decision* surface instead: an admin
    # gets `GET /forecast/budget` (the same projection burned down against the
    # configured cap) and a client gets `GET /forecast/domain`. The un-burned-down
    # series is the top panel phase 7 adds for the platform admin.
    ("GET", "/forecast/usage"): (
        "phase 7 — the UI serves /forecast/budget: the same series, burned down "
        "against the configured cap"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def portal_endpoints() -> dict[tuple[str, str], set[Endpoint]]:
    """Endpoints reachable from each ``(role, section)`` the portals expose."""
    assert _WEB_SRC.is_dir(), f"web sources missing at {_WEB_SRC}"
    api = _api_endpoints_by_export()
    components = _page_component_modules()
    console = _console_sections()
    out: dict[tuple[str, str], set[Endpoint]] = {}
    for role, sections in _role_sections().items():
        for section in sections:
            component = _component_for(role, section, console)
            assert component is not None, (
                f"portal '{role}' lists section '{section}', but "
                f"{_SECTION_PAGE.name} mounts nothing for it — the route 404s."
            )
            module = components.get(component)
            assert module is not None, (
                f"{_SECTION_PAGE.name} mounts <{component}> for {role}/{section} "
                "but does not import it from a resolvable module."
            )
            out[(role, section)] = _endpoints_reachable_from(module, api)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_every_backend_call_goes_through_the_api_layer() -> None:
    """No module outside ``web/src/lib/api`` may call ``fetch`` directly.

    This is the assumption the whole coverage analysis rests on. A component that
    fetched an endpoint itself would be invisible to the import-graph walk, and the
    reachability test would start reporting false gaps (or, worse, be "fixed" by
    allowlisting a route that is in fact live).
    """
    offenders = [
        str(path.relative_to(_WEB_SRC))
        for path in sorted(_WEB_SRC.rglob("*.ts*"))
        if _API_DIR not in path.parents
        and re.search(r"\bfetch\s*\(", _code_only(path.read_text()))
    ]
    assert not offenders, (
        "these modules call fetch() outside web/src/lib/api, so their endpoints are "
        f"invisible to the route-coverage analysis: {offenders}"
    )


def _code_only(text: str) -> str:
    """Blank out comments and string bodies so a word search sees only code.

    One pass, sharing :func:`_string_literals`' state machine: stripping comments
    first would mistake the ``//`` inside a ``'https://…'`` literal for a comment
    and blank the rest of that line.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(start: int, end: int) -> None:
        for k in range(start, min(end, n)):
            out[k] = " "

    while i < n:
        char = text[i]
        if char == "/" and text[i + 1 : i + 2] == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if char == "/" and text[i + 1 : i + 2] == "*":
            end = text.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
            continue
        if char not in "\"'`":
            i += 1
            continue
        delim, j = char, i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == delim:
                break
            j += 1
        blank(i, j + 1)
        i = j + 1
    return "".join(out)


def test_every_non_public_endpoint_is_reachable_from_a_portal(
    portal_endpoints: dict[tuple[str, str], set[Endpoint]],
) -> None:
    """Every authenticated route is served by some portal, or allowlisted with a reason."""
    non_public, _public = _served_endpoints()
    reachable: set[Endpoint] = set()
    for endpoints in portal_endpoints.values():
        reachable |= endpoints

    unreachable = {
        endpoint
        for endpoint in non_public
        if endpoint not in reachable
        and (endpoint.method, endpoint.path) not in UNREACHABLE_BY_DESIGN
    }
    assert not unreachable, (
        "these authenticated endpoints are reachable from no portal — wire each to a "
        "section, or add it to UNREACHABLE_BY_DESIGN with the reason it is "
        f"deliberately unreachable: {sorted(str(e) for e in unreachable)}"
    )


def test_allowlist_is_neither_stale_nor_wrong(
    portal_endpoints: dict[tuple[str, str], set[Endpoint]],
) -> None:
    """Every allowlist entry names a real route that is genuinely unreachable."""
    non_public, public = _served_endpoints()
    served = {(e.method, e.path) for e in non_public | public}
    reachable = {
        (e.method, e.path)
        for endpoints in portal_endpoints.values()
        for e in endpoints
    }

    missing = sorted(key for key in UNREACHABLE_BY_DESIGN if key not in served)
    assert not missing, (
        f"UNREACHABLE_BY_DESIGN names routes the app no longer serves: {missing}"
    )

    wired = sorted(key for key in UNREACHABLE_BY_DESIGN if key in reachable)
    assert not wired, (
        "these routes are now reachable from a portal — delete their "
        f"UNREACHABLE_BY_DESIGN entries: {wired}"
    )


def test_every_role_can_reach_every_section_it_should_have() -> None:
    """Each role's portal exposes — and really renders — the sections it must have."""
    role_sections = _role_sections()
    defined = _section_ids()
    console = _console_sections()

    assert set(role_sections) == set(REQUIRED_SECTIONS), (
        "the portal roles and the required-section table disagree: "
        f"{sorted(role_sections)} vs {sorted(REQUIRED_SECTIONS)}"
    )

    for role, required in REQUIRED_SECTIONS.items():
        exposed = set(role_sections[role])
        assert required <= exposed, (
            f"portal '{role}' is missing required sections: {sorted(required - exposed)}"
        )
        undefined = exposed - defined
        assert not undefined, (
            f"portal '{role}' lists sections with no SECTIONS entry: {sorted(undefined)}"
        )
        for section in sorted(exposed):
            assert _component_for(role, section, console) is not None, (
                f"portal '{role}' lists section '{section}' but "
                f"{_SECTION_PAGE.name} renders nothing for it — the route 404s."
            )


def test_the_client_portal_can_ask_a_question(
    portal_endpoints: dict[tuple[str, str], set[Endpoint]],
) -> None:
    """The client's console really reaches ``POST /query`` (§3.10, the actual gap).

    The catalogue entry alone proves nothing: the renderer must mount the console
    for ``client`` and that component must reach the run endpoint.
    """
    assert ("client", "console") in portal_endpoints, (
        "the client portal exposes no 'console' section — the role the product "
        "exists for cannot ask a question."
    )
    endpoints = portal_endpoints[("client", "console")]
    assert Endpoint("POST", "/query") in endpoints, (
        "the client console does not reach POST /query; it reaches "
        f"{sorted(str(e) for e in endpoints)}"
    )
