"""The public standards summary — ``GET /platform/standards``.

Why this route exists at all
----------------------------
The landing page at ``/`` is public and unauthenticated. It wants to say which
published frameworks Aegis is *built to* and how far each one actually goes — and
the only honest way to say that is to read the same table the DevOps compliance
screen reads. ``GET /compliance`` cannot serve it: that route is guarded by
:func:`app.api.routes.require_platform_security_reader`, and correctly so, because
its body is a **control-by-control gap map**. "``LLM04`` is not implemented, here is
the file that would have implemented it" is a target list, and a public page has no
business publishing one.

So this module serves the *summary* projection of exactly the same data: the
framework names, the jurisdiction each belongs to, the four derived state counts per
framework plus the totals, and — since the landing page stopped claiming frameworks
and started naming controls — the id and title of every control whose state is
``enforced``. What it deliberately does **not** carry:

* no control in any state other than ``enforced``. A ``partial``, a
  ``not_implemented`` or a ``not_applicable`` control is never named here, which is
  the whole of the gap map and the whole of the reason ``/compliance`` is guarded;
* no ``summary`` and no **``gap``** on the controls it does name;
* no ``evidence`` — no file paths, route names or pytest node ids;
* no ``residency`` — where this deployment's stores resolve is live configuration,
  and the authenticated ``/compliance`` response is the right place for it.

Naming the enforced controls is a claim a reader can check against the published
standard and hold us to. It is the opposite of a target list — see
:class:`EnforcedControl` for why the residue it leaves is one ``coverage`` already
published.

The counts are the point, and they are **derived, never typed**
-------------------------------------------------------------
Every number here comes from :func:`app.platform.compliance.build_compliance`, which
counts the four states over the real control entries. Add a framework to
``_FRAMEWORKS``, flip one control from ``partial`` to ``enforced``, and this
response changes on the next request with no edit in this module, in the frontend,
or in the landing page's copy. ``backend/tests/api/test_standards.py`` asserts that
equality directly, so a summary that drifted from the authority would fail the suite
rather than reach a jury. A hardcoded "27 enforced" that silently rots into a lie is
the exact defect this platform exists to avoid, and a landing page is the worst place
to commit it.

Readiness, not certification
----------------------------
:data:`~app.platform.compliance.DISCLAIMER` rides on every response, unchanged and
unabridged, for the same reason it rides on ``/compliance``: Aegis holds no ISO
27001 certificate, no ISO/IEC 42001 certificate, no SOC 2 report and no EU AI Act
conformity assessment, and a readiness figure that travelled without saying so would
be a claim a reviewer can puncture with one question.

Costs nothing: pure data assembly, no database round-trip, no model call.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.platform.compliance import (
    DISCLAIMER,
    DOC_REF,
    ControlState,
    FrameworkCoverage,
    build_compliance,
)

__all__ = [
    "EnforcedControl",
    "FrameworkSummary",
    "StandardsResponse",
    "build_standards",
    "mount",
    "standards_router",
]


#: Short display marks, keyed by the framework id in ``app.platform.compliance``.
#:
#: The full ``name`` is written for a control table — "India DPDP Act 2023 and DPDP
#: Rules 2025" is the right label above a twelve-row grid and the wrong one inside a
#: 120px wordmark cell. These are the same names trimmed to what a reader recognises,
#: and nothing else: no re-branding, no acronym a framework does not use for itself.
#:
#: **An unknown id falls back to its full ``name``**, deliberately. Another agent is
#: extending ``_FRAMEWORKS`` right now; a framework this map has not met yet must
#: still appear on the page — long label and all — rather than silently vanish from a
#: grid whose whole argument is that it is complete.
_MARKS: dict[str, str] = {
    "dpdp": "DPDP Act",
    "cert-in": "CERT-In",
    "india-sectoral": "MeitY · RBI · SEBI · BIS",
    "owasp-llm": "OWASP LLM Top 10",
    "owasp-web": "OWASP Top 10",
    "mitre-atlas": "MITRE ATLAS",
    "nist-ai-rmf": "NIST AI RMF",
    "iso-42001": "ISO/IEC 42001",
    "iso-27001": "ISO/IEC 27001",
    "eu-ai-act": "EU AI Act",
    "soc2": "SOC 2 TSC",
    "privacy": "GDPR",
}


class EnforcedControl(BaseModel):
    """One control this build **enforces**, named so a reader can check the claim.

    Two fields and no third. ``summary`` is a sentence about the mechanism, ``evidence``
    names the files, routes and pytest node ids behind it, and ``gap`` is the thing this
    module exists to withhold; none of them travel here. What a public reader gets is
    the framework's own identifier and the framework's own words for it — enough to look
    the control up in the published standard and ask us about it, and nothing more.

    **Why naming these is not the gap map the module refuses to serve.** The rule is
    that no control is ever named unless its state is ``enforced``: a reader learns what
    Aegis *does*, never what it does not. The residue — that a framework's unnamed
    controls are the ones not enforced — was already public in ``coverage`` before this
    field existed ("5 enforced of 10" says five are not), and the residue never
    distinguishes ``partial`` from ``not_implemented``, so it never points at a hole.
    The inversion that would be a target list is naming the *other* three states, and
    that is exactly what :func:`build_standards` filters out.
    """

    id: str = Field(description="The framework's own control identifier, e.g. 'Art. 14'.")
    title: str = Field(description="The control's name, as the framework words it.")


class FrameworkSummary(BaseModel):
    """One framework's public row: what it is called, whose law it is, how far it goes."""

    id: str = Field(description="Stable slug — the same id `GET /compliance` uses.")
    mark: str = Field(
        description=(
            "Short display label for a wordmark grid. Falls back to the full name for a "
            "framework this build has no short mark for."
        )
    )
    name: str = Field(description="The framework's full name.")
    version: str = Field(description="The edition these controls are taken from.")
    jurisdiction: str = Field(description="'India' or 'International'.")
    coverage: FrameworkCoverage = Field(
        description="The four derived state counts for this framework, and its total."
    )
    enforced_controls: list[EnforcedControl] = Field(
        default_factory=list,
        description=(
            "Every control of this framework whose state is 'enforced', in the order the "
            "authority lists them — id and title only. Controls in any other state are "
            "absent, as are all summaries, gaps and evidence references. The list length "
            "always equals coverage.enforced."
        ),
    )


class StandardsResponse(BaseModel):
    """Body for ``GET /platform/standards`` — counts and names, never control detail."""

    disclaimer: str = Field(
        description=(
            "Readiness, not certification. Always present, always rendered — a summary "
            "that travelled without it would be the one defect this surface avoids."
        )
    )
    certified: bool = Field(
        default=False,
        description=(
            "Whether any framework below has been independently certified or attested. "
            "Always false, and served as a field rather than assumed, so a client cannot "
            "render a certification claim by forgetting to read the disclaimer."
        ),
    )
    doc_ref: str = Field(description="The written authority these counts project.")
    generated_at: str = Field(description="ISO-8601 UTC timestamp of this read.")
    frameworks: list[FrameworkSummary] = Field(
        description="Every mapped framework, in the served order — India first."
    )
    coverage: FrameworkCoverage = Field(description="Totals across every framework.")


def build_standards() -> StandardsResponse:
    """Return the public standards summary, derived from the compliance authority.

    Calls :func:`app.platform.compliance.build_compliance` and drops every field a
    public reader must not have — the control entries, their gaps and their evidence
    references, and the residency inventory — keeping the names, the jurisdictions
    and the counts that were derived from those very entries.

    Returns:
        A :class:`StandardsResponse` whose every number was counted from the real
        control table on this call, carrying
        :data:`~app.platform.compliance.DISCLAIMER` verbatim.
    """
    full = build_compliance()
    return StandardsResponse(
        disclaimer=DISCLAIMER,
        certified=False,
        doc_ref=DOC_REF,
        generated_at=full.generated_at,
        frameworks=[
            FrameworkSummary(
                id=framework.id,
                mark=_MARKS.get(framework.id, framework.name),
                name=framework.name,
                version=framework.version,
                jurisdiction=framework.jurisdiction,
                coverage=framework.coverage,
                # The one filter that makes naming controls publishable: `state is
                # ENFORCED`, evaluated here rather than trusted from a caller. A control
                # that slips out of `enforced` disappears from this list on the next
                # request, which is what lets the landing page name controls at all —
                # the page holds ids it wants to show and prints only the ones this
                # response still carries.
                enforced_controls=[
                    EnforcedControl(id=control.id, title=control.title)
                    for control in framework.controls
                    if control.state is ControlState.ENFORCED
                ],
            )
            for framework in full.frameworks
        ],
        coverage=full.coverage,
    )


standards_router = APIRouter()


@standards_router.get(
    "/platform/standards", response_model=StandardsResponse, tags=["platform"]
)
async def platform_standards() -> StandardsResponse:
    """Return the public standards-alignment summary — names, jurisdictions, counts.

    **Unauthenticated by design.** The public landing page renders this band, so it
    must answer without a bearer token. It carries no tenant, user, usage or
    credential data, and — unlike ``GET /compliance``, which it summarises — no
    control-level gap or evidence reference either.

    **Alignment, not certification.** ``certified`` is always ``false`` and
    ``disclaimer`` says why on every response. Aegis holds no ISO 27001 certificate,
    no ISO/IEC 42001 certificate, no SOC 2 report and no EU AI Act conformity
    assessment; nothing here has been audited by an independent party.

    Every count is derived from the control table on each request, so the figure a
    visitor reads is the figure the repository can defend.
    """
    return build_standards()


def mount(target: APIRouter) -> None:
    """Attach this module's route to ``target`` as a real ``APIRoute``.

    Idempotent for the reason :func:`app.api.routes_compliance.mount` is: this module
    is mounted from the composition root while :mod:`app.api.routes` is being edited
    elsewhere, and mounting twice would leave a second, shadowed copy of the handler
    in the served table.

    Args:
        target: The application's main router; its ``routes`` list is extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ())) for route in target.routes
    }
    target.routes.extend(
        route
        for route in standards_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
