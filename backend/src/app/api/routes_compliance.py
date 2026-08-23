"""The compliance-readiness control plane — ``GET /compliance``.

One read-only route in a module of its own, mounted from the composition root the
same way :mod:`app.api.routes_redteam` and :mod:`app.api.routes_reports` are. It
serves :func:`app.platform.compliance.build_compliance` — the typed projection of
``docs/compliance/README.md`` — so the Compliance screen renders a written position
rather than prose typed into a React component.

``GET /compliance``
    Every mapped framework, each control's four-valued state
    (``enforced`` / ``partial`` / ``not_implemented`` / ``not_applicable``), the
    evidence behind it, and the derived coverage counts. The response always carries
    :data:`~app.platform.compliance.DISCLAIMER`: this is readiness evidence, not
    certification, and a response that travelled without saying so would be the one
    defect this surface exists to avoid.

Guarded by :func:`app.api.routes.require_platform_security_reader` — the same rule
the security posture and the red-team battery already use, because this is a fact
about the *deployment*, not about any one tenant, and a tenant admin has no business
reading the platform's own control map.

Nothing here dials anything: the module is pure data assembly, so a read costs no
database round-trip and no model call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.routes import AuthContext, require_platform_security_reader
from app.platform.compliance import ComplianceResponse, build_compliance

__all__ = ["compliance_router", "mount"]

compliance_router = APIRouter()


@compliance_router.get("/compliance", response_model=ComplianceResponse, tags=["platform"])
async def compliance(
    auth: AuthContext = Depends(require_platform_security_reader),
) -> ComplianceResponse:
    """Return the framework-by-framework compliance-readiness map (platform staff).

    Nine frameworks — OWASP LLM Top 10 (2025), OWASP Top 10:2025, MITRE ATLAS, NIST AI
    RMF 1.0, ISO/IEC 42001 Annex A, ISO/IEC 27001:2022 Annex A, the EU AI Act, SOC 2
    Trust Services Criteria, and GDPR/DPDP — each control carrying a four-valued state
    and the files, routes and tests that back it.

    **Not a certification.** ``disclaimer`` says so on every response, and
    ``doc_ref`` names the written authority the payload projects. Every evidence
    reference is resolved against the real filesystem, the real route table and the
    real test files by ``backend/tests/api/test_compliance.py``, so a claim naming a
    file that does not exist fails the suite instead of reaching a reader.
    """
    return build_compliance()


def mount(target: APIRouter) -> None:
    """Attach this module's route to ``target`` as a real ``APIRoute``.

    Idempotent for the reason :func:`app.api.routes_redteam.mount` is: this module is
    mounted from the composition root while :mod:`app.api.routes` is being edited
    elsewhere, and mounting twice would leave a second, shadowed copy of the handler
    in the served table — invisible at runtime and confusing in exactly the place the
    route-coverage test reads.

    Args:
        target: The application's main router; its ``routes`` list is extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ())) for route in target.routes
    }
    target.routes.extend(
        route
        for route in compliance_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
