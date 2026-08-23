"""The governance artefacts are a control, so they are tested like one.

``docs/governance/`` is what moves NIST AI RMF's **GOVERN** and **MAP** functions to
``enforced`` in :mod:`app.platform.compliance`. Those two functions are documentation
and process — that is the framework's own intended form of compliance for them — which
means the *documents* are the mechanism, exactly as ``aegis/governance/rls.py`` is the
mechanism behind a tenancy row.

A file-exists assertion would not be a control. Three things are checked instead, and
each one closes a specific way a governance pack goes bad:

* **Every repository path a document cites is resolved against the real filesystem.**
  This is the same rule ``test_compliance.py`` applies to every evidence reference, and
  it is what stops a policy clause from outliving the mechanism it names. Rename
  ``approvals.py`` and the AI policy's human-oversight clause fails here rather than
  sitting in the repository as a false claim.
* **Each document carries the specific commitments its control claims.** The compliance
  row for GOVERN says there is an owner per role, a named cadence and a triage path;
  the row for MAP says the affected individuals are identified. A test that only
  counted bytes would let any of those be deleted.
* **The absences stay written down.** A governance pack drifts toward flattery one
  deletion at a time, and the sentences most likely to be tidied away are the ones
  admitting that CERT-In's six-hour clock is not automated, that nothing is encrypted
  at rest, and that four harms have no mitigation at all. Those are pinned.

Deleting or emptying any of these documents fails this module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOVERNANCE = _REPO_ROOT / "docs" / "governance"

#: The artefacts, and the minimum size below which a document is a stub rather than a
#: control. Generous — the point is to catch a file emptied or replaced by a heading,
#: not to legislate a word count.
_ARTEFACTS: dict[str, int] = {
    "README.md": 1_500,
    "ai-policy.md": 6_000,
    "accountable-roles.md": 4_000,
    "incident-response.md": 6_000,
    "review-cadence.md": 3_000,
    "context-and-impact.md": 8_000,
    "incidents/README.md": 800,
}

#: Every markdown document in the pack, as (name, text) — read once.
_DOCS: dict[str, str] = {
    name: (_GOVERNANCE / name).read_text(encoding="utf-8")
    for name in _ARTEFACTS
    if (_GOVERNANCE / name).exists()
}


def _doc(name: str) -> str:
    """Return one artefact's text, failing loudly if the artefact is missing."""
    assert name in _DOCS, f"docs/governance/{name} is missing — the control is the document."
    return _DOCS[name]


#: A backticked span that looks like a path into this repository: it starts with one of
#: the real top-level directories (or is a known root file) and carries an extension.
#: Deliberately anchored on the directory names rather than on "contains a slash", so a
#: URL, a settings key (``agent.gate_min_risk``) or a SQL phrase is never mistaken for a
#: file and silently "resolved".
_PATH_SPAN = re.compile(
    r"`((?:aegis|backend|web|docs|scripts|tests|spikes|testdata|\.github)/[\w./\-]+"
    r"(?:::[\w]+)?|SKILL\.md|README\.md|AGENTS\.md|DESIGN\.md)`"
)


def _cited_paths(text: str) -> set[str]:
    """Return every repository path cited in ``text``, with pytest node ids trimmed."""
    return {
        match.group(1).partition("::")[0]
        for match in _PATH_SPAN.finditer(text)
        # A bare README.md / SKILL.md inside a link target is relative to docs/governance
        # and is checked by the link test below instead.
        if "/" in match.group(1) or match.group(1) in {"SKILL.md", "AGENTS.md", "DESIGN.md"}
    }


# ─────────────────────────────────────────────────────────────────────────────
# The pack exists, and is not a set of stubs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "minimum"), sorted(_ARTEFACTS.items()))
def test_every_governance_artefact_exists_and_is_substantial(name: str, minimum: int) -> None:
    """A missing or emptied artefact is a GOVERN/MAP control that stopped existing."""
    path = _GOVERNANCE / name
    assert path.exists(), (
        f"docs/governance/{name} is missing. NIST AI RMF GOVERN and MAP are claimed "
        "'enforced' on the strength of these documents; without one, that claim is false."
    )
    text = path.read_text(encoding="utf-8")
    assert len(text) >= minimum, (
        f"docs/governance/{name} is {len(text)} bytes, below the {minimum} a real "
        "document takes. A stub with the right filename is the failure this checks for."
    )
    assert text.lstrip().startswith("#"), f"docs/governance/{name} has no heading."


def test_every_repository_path_a_governance_document_cites_exists() -> None:
    """The policy's clauses name mechanisms; a renamed mechanism must fail here.

    This is the rule that makes these documents citable as a control rather than as a
    promise, and it is the same one ``test_compliance.py`` applies to every evidence
    reference on the compliance surface.
    """
    missing: list[str] = []
    checked = 0
    for name, text in _DOCS.items():
        for ref in _cited_paths(text):
            checked += 1
            if not (_REPO_ROOT / ref).exists():
                missing.append(f"{name} cites {ref!r}, which does not exist")
    assert not missing, "\n".join(missing)
    # A regex that stopped matching would make the assertion above vacuously true.
    assert checked >= 40, (
        f"only {checked} repository paths were resolved across the governance pack; the "
        "documents are supposed to ground their clauses in named mechanisms."
    )


def test_every_internal_link_between_the_artefacts_resolves() -> None:
    """The pack cross-references itself; a broken link is a document nobody reaches."""
    broken: list[str] = []
    for name, text in _DOCS.items():
        base = (_GOVERNANCE / name).parent
        for target in re.findall(r"\]\((?!https?:)([^)#]+)(?:#[^)]*)?\)", text):
            if not (base / target).resolve().exists():
                broken.append(f"{name} links to {target!r}, which does not resolve")
    assert not broken, "\n".join(broken)


# ─────────────────────────────────────────────────────────────────────────────
# GOVERN — the four things the control claims
# ─────────────────────────────────────────────────────────────────────────────


def test_the_ai_policy_states_a_use_boundary_a_sourcing_position_and_oversight() -> None:
    """GOVERN claims a written AI policy. These are the clauses that make it one."""
    text = _doc("ai-policy.md")
    assert "## 1. What Aegis is for" in text
    assert "## 2. What Aegis may not be used for" in text, (
        "a policy with no prohibited-use section states a purpose, not a boundary"
    )
    assert "## 5. Model sourcing" in text
    # The oversight requirement, and the two halves that make it more than a sentence:
    # something fires the gate, and a timeout is not an approval.
    assert "agent.gate_min_risk" in text
    assert "test_sla_sweeper_expires_and_auto_rejects_high" in text, (
        "the policy's human-oversight clause must cite the test proving the gate is "
        "fail-safe rather than fail-open"
    )
    # Model sourcing: one gateway, no downloaded weights, no training on tenant data.
    for clause in ("gateway", "weights", "train"):
        assert clause in text.lower(), f"the model-sourcing position says nothing about {clause}"


def test_the_role_register_names_an_owner_for_every_role_the_software_enforces() -> None:
    """GOVERN claims an accountable-role register. Five roles, each with an owner.

    The roles are read from ``web/src/lib/portal.ts`` rather than typed here, so a sixth
    portal added to the product fails this test until somebody says who is accountable
    for it.
    """
    text = _doc("accountable-roles.md")
    portal = (_REPO_ROOT / "web" / "src" / "lib" / "portal.ts").read_text(encoding="utf-8")
    # ``PORTALS: Portal[] = [`` — split on the assignment, not on the first ``]``, which
    # belongs to the type annotation.
    block = portal.partition("export const PORTALS")[2].partition("= [")[2].partition("]")[0]
    roles = re.findall(r"'([a-z_]+)'", block)
    assert len(roles) == 5, f"expected the five portals, found {roles}"
    for role in roles:
        assert role in text, (
            f"{role!r} is a portal the software enforces and the register does not name it."
        )
    # A register that lists roles without naming what bounds each one is an org chart.
    for guard in ("require_platform_admin", "require_tenant_admin", "require_infra_reader"):
        assert guard in text, f"the register does not cite the {guard} guard."
    # And the part that is uncomfortable to write down.
    assert "two-person" in text and "separation of duties" in text, (
        "the register must say who actually holds these roles today"
    )


def test_the_incident_plan_has_detection_triage_containment_and_review() -> None:
    """GOVERN claims an incident-response plan. All five stages, keyed to real signals."""
    text = _doc("incident-response.md")
    lowered = text.lower()
    for stage in ("detection", "triage", "containment", "notification", "review"):
        assert stage in lowered, f"the plan has no {stage} stage."
    # Severity levels, or "triage" is a word with no output.
    for severity in ("S1", "S2", "S3", "S4"):
        assert severity in text, f"the plan defines no {severity} severity."
    # The signals it triages on must be the ones this system emits.
    for signal in ("audit_log", "/readyz", "redteam", "security/posture", "sla_sweeper"):
        assert signal in lowered or signal in text, (
            f"the plan does not name {signal!r} — a plan detached from the signals the "
            "platform actually emits is a template."
        )
    # A post-incident review with no clock is not a cadence.
    assert "five working days" in lowered


def test_the_incident_plan_admits_the_six_hour_clock_is_not_automated() -> None:
    """The sentence most likely to be tidied away, pinned where it cannot be.

    CERT-In Direction (ii) requires a reportable incident to reach CERT-In within six
    hours. This plan supplies a definition and an accountable person and **not** the
    reporting path, and the compliance row stays ``not_implemented`` because of it. A
    governance pack that quietly dropped this line would be claiming the opposite.
    """
    text = _doc("incident-response.md")
    assert "CERT-In" in text
    assert "6-hour" in text or "six hours" in text
    assert "not automated" in text, "the plan must say plainly that the clock is manual"
    assert "not_implemented" in text, "the plan must say which compliance row it does not move"
    assert "backup" in text.lower(), (
        "recovery is a stage of this plan and there is no backup or restore; saying so is "
        "the difference between a plan and a wish"
    )


def test_the_review_cadence_names_a_period_and_an_owner_for_every_artefact() -> None:
    """GOVERN claims a review cadence. Every artefact reviewed, by somebody, on a clock."""
    text = _doc("review-cadence.md")
    for artefact in _ARTEFACTS:
        if artefact == "README.md":
            continue
        assert artefact in text, f"{artefact} has no entry in the review cadence."
    lowered = text.lower()
    assert "quarterly" in lowered and "6 months" in lowered, "no named period"
    assert "platform owner" in lowered and "devops" in lowered, "no named reviewer"
    # A cadence with no trigger only catches drift, never breakage.
    assert "off-cycle" in lowered
    assert "domain swap" in lowered, (
        "retargeting the adapter changes who is affected; it is the one trigger this "
        "platform cannot omit"
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAP — context of use, and impact on affected individuals
# ─────────────────────────────────────────────────────────────────────────────


def test_the_impact_assessment_identifies_the_people_affected() -> None:
    """MAP claims an assessment of impact on affected individuals. Name them first."""
    text = _doc("context-and-impact.md")
    assert "## 2. Who is affected" in text
    lowered = text.lower()
    assert "end customer" in lowered, (
        "the affected party is a tenant's end customer, whose service requests and "
        "documents this system processes; an assessment that names only the operator has "
        "assessed the wrong person"
    )
    assert "third part" in lowered, (
        "a person named in a case note never interacted with anyone and is still processed"
    )
    # Context of use: the deployment context, not just the risk list.
    assert "## 1. What the system is, in context" in text
    for question in ("propose", "deploy", "not"):
        assert question in lowered


def test_the_impact_assessment_pairs_every_harm_with_a_mitigation_or_says_there_is_none() -> None:
    """A harm table with a mitigation in every cell is a table nobody checked.

    Each ``### 4.x`` harm must carry a ``Mitigation`` line, and every mitigation must
    either cite a mechanism in this repository or say ``NONE``. Four of them say NONE,
    and that is what makes the other nine believable.
    """
    text = _doc("context-and-impact.md")
    harms = re.findall(r"^### 4\.\d+ .+?(?=^### |^## )", text, re.MULTILINE | re.DOTALL)
    assert len(harms) >= 8, f"only {len(harms)} harms assessed; that is not an assessment"

    unmitigated = 0
    for harm in harms:
        title = harm.splitlines()[0]
        assert "**Who bears it:**" in harm, f"{title} does not say who bears the harm"
        assert "**Mitigation" in harm or "Mitigation: NONE" in harm, (
            f"{title} names no mitigation and does not say there is none"
        )
        if "NONE" in harm:
            unmitigated += 1
        else:
            assert re.search(r"`(?:aegis|backend|web|docs)/[\w./\-]+`", harm), (
                f"{title} claims a mitigation without citing a mechanism in this repository"
            )
    assert unmitigated >= 3, (
        "every harm in this system has a mitigation is not a credible finding; the "
        "assessment must keep the ones that do not"
    )

    # The four specific absences that cost the individual, not the operator.
    lowered = text.lower()
    assert "encrypted at rest" in lowered
    assert "no grievance channel" in lowered or "grievance" in lowered
    assert "never told ai was involved" in lowered or "never told" in lowered


def test_the_impact_assessment_states_what_it_did_not_cover() -> None:
    """Scope is half of an assessment; an unbounded one cannot be relied on."""
    text = _doc("context-and-impact.md")
    assert "## 6. What this assessment did not cover" in text
    lowered = text.lower()
    assert "fairness" in lowered, (
        "no fairness evaluation exists; 'no bias was found' would mean 'none was looked for'"
    )
    assert "not a dpia" in lowered, (
        "a risk assessment is not a DPIA and must not be allowed to read as one"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The pack and the compliance surface agree
# ─────────────────────────────────────────────────────────────────────────────


def test_the_compliance_surface_cites_this_pack_for_govern_and_map() -> None:
    """The documents and the control table must not drift apart.

    Imported here rather than at module scope so the artefact tests above still run and
    report usefully if the compliance module is mid-edit.
    """
    from app.platform.compliance import ControlState, build_compliance

    nist = next(f for f in build_compliance().frameworks if f.id == "nist-ai-rmf")
    rows = {control.id: control for control in nist.controls}

    govern = rows["GOVERN"]
    assert govern.state is ControlState.ENFORCED
    cited = {evidence.ref for evidence in govern.evidence}
    for required in (
        "docs/governance/ai-policy.md",
        "docs/governance/accountable-roles.md",
        "docs/governance/incident-response.md",
        "docs/governance/review-cadence.md",
    ):
        assert required in cited, f"GOVERN claims 'enforced' without citing {required}."

    map_row = rows["MAP"]
    assert map_row.state is ControlState.ENFORCED
    assert "docs/governance/context-and-impact.md" in {
        evidence.ref for evidence in map_row.evidence
    }, "MAP claims 'enforced' without citing the context-of-use and impact assessment."
