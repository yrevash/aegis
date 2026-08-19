"""The conformance suite is proved the only way that means anything: by running it.

Two claims, and neither is credible from reading the checks:

* ``pytest --pyargs aegis.conformance`` **passes** against the reference adapter, and
* it **fails, specifically**, against a deliberately mis-wired one — ``broken_adapter/``
  next door, where every break is a plausible first attempt rather than a caricature.

Twelve of the fourteen checks fail against that fixture and two pass, which is the part
that stops this being a tautology: a check that cannot fail is decoration, and a suite
that fails everything the moment anything is wrong tells an integrator nothing about
where to look.

The fixture is **self-contained** — it imports nothing from the production adapter — and
that is asserted here, because it once was not. Its memory break was that the *shipped*
hints named a playbook this directory had renamed, so a correct retarget of the
production adapter re-pointed those literals and the break evaporated: the run went from
``12 failed, 1 passed`` to ``11 failed, 2 passed`` on a change that had nothing to do
with it, and the meta-test was the only thing that noticed.

Each run is a real subprocess with the real command line, because the command line is
half of what is being verified — the ``pytest11`` entry point, the ``--aegis-adapter``
option it registers, and the exit code an integrator's CI reads.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_BACKEND_SRC = _HERE.parents[1] / "src"

#: The checks that must fail against ``broken_adapter`` — one per break in the fixture.
EXPECTED_FAILURES = frozenset(
    {
        "test_every_contract_member_is_present",
        "test_domain_identity_is_a_usable_topical_rail",
        "test_every_roster_role_has_a_handler_node",
        "test_the_roster_default_role_is_declared_and_routable",
        "test_every_tool_declares_a_risk_tier",
        "test_allowlists_name_registered_tools_and_known_personas",
        "test_every_persona_the_adapter_declares_resolves",
        "test_the_system_prompt_never_drops_the_platform_floor",
        "test_memory_spec_satisfies_the_memory_contract",
        "test_every_playbook_is_reachable_from_select_skills",
        "test_ml_spec_resolves_to_the_domain_not_the_fallback",
        "test_seed_corpus_records_carry_identity_and_chunk",
    }
)

#: The two checks ``broken_adapter`` does not break: its ``skills/`` directory is intact,
#: and the vocabulary check reads the *core*, which a broken adapter cannot dirty.
EXPECTED_PASSES = frozenset(
    {
        "test_skills_directory_holds_at_least_one_playbook",
        "test_no_shipped_domain_vocabulary_survives_outside_the_adapter",
    }
)


def _run_conformance(*args: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the conformance suite in a subprocess, from a directory of its own."""
    env = os.environ.copy()
    env.pop("AEGIS_ADAPTER", None)
    env["PYTHONPATH"] = os.pathsep.join([str(_BACKEND_SRC), str(_HERE)])
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--pyargs",
            "aegis.conformance",
            "-q",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=300,
        check=False,
    )


@pytest.fixture(scope="module")
def broken_run(tmp_path_factory):
    """One run against ``broken_adapter``, shared by every assertion about its output."""
    return _run_conformance(
        "--aegis-adapter",
        "broken_adapter",
        tmp_path=tmp_path_factory.mktemp("conformance"),
    )


def test_the_reference_adapter_passes_conformance(tmp_path):
    result = _run_conformance("--aegis-adapter", "app.adapter", tmp_path=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "14 passed" in result.stdout


def test_a_mis_wired_adapter_fails_check_by_check(broken_run):
    result = broken_run
    assert result.returncode != 0
    failed = {
        line.rsplit("::", 1)[-1].split(" - ")[0].strip()
        for line in result.stdout.splitlines()
        if line.startswith("FAILED ")
    }
    assert failed == set(EXPECTED_FAILURES), sorted(failed)
    assert not (failed & EXPECTED_PASSES)
    assert "12 failed, 2 passed" in result.stdout


@pytest.mark.parametrize(
    ("phrase", "why"),
    [
        ("agent_roster() declares 'answer', 'triage'", "names the offending values"),
        ("aegis.agent.graph.SPECIALIST_NODES", "names the edit to make"),
        ("answered by the 'qa' pipeline", "names the consequence of leaving it"),
        ("logger.warning rather than raising", "names the defect it descends from"),
    ],
)
def test_a_failure_says_what_fix_consequence_and_scar(broken_run, phrase, why):
    """A failure an integrator can act on without opening the docs — the whole point."""
    assert phrase in broken_run.stdout, f"the roster failure no longer {why}"


def test_naming_no_adapter_is_one_usage_error_not_thirteen(tmp_path):
    result = _run_conformance(tmp_path=tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "was not told which adapter to check" in output
    assert "AEGIS_ADAPTER=myapp.adapter" in output
    assert "ERROR aegis" not in output  # one usage error, not one error per check


def test_the_broken_fixture_imports_no_production_domain_code():
    """The fixture is self-contained, so a retarget cannot dissolve the breaks it proves.

    This assertion *is* the fix: the fixture used to import ``select_skills``, the fact
    models, the tool registry, the roster types and the corpus loader from
    ``app.adapter``, so its intended memory break — the shipped hints naming a playbook
    this directory had renamed — was a property of the shipped domain rather than of the
    fixture. Re-point those literals, as any correct retarget does, and the break is
    gone with nothing to say so.
    """
    sources = sorted((_HERE / "broken_adapter").rglob("*.py"))
    assert sources, "the broken_adapter fixture has no sources"
    offenders = [
        f"{path.name}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith(("import app", "from app")) or " from app." in line
    ]
    assert not offenders, (
        "the broken adapter must not import the production domain — "
        f"found {offenders}"
    )


def _stub_spec(tmp_path: Path, hints: dict[str, str], playbooks: tuple[str, ...]):
    """Return a memory-spec-shaped module whose keyword table is a MODULE constant."""
    import types

    skills = tmp_path / "skills"
    skills.mkdir(exist_ok=True)
    for name in playbooks:
        (skills / f"{name}.md").write_text(f"# {name}\n\nA playbook.\n", encoding="utf-8")
    module = types.ModuleType("stub_memory_spec")
    module.SKILLS_DIR = str(skills)
    module.SKILL_HINTS = dict(hints)  # hoisted out of the function — the F4 blind spot

    def select_skills(query, persona_id, available):
        chosen = [s for k, s in module.SKILL_HINTS.items() if k in query.lower()]
        return [s for s in chosen if s in available] or None

    module.select_skills = select_skills
    return module


def _personas_stub():
    """A personas-shaped module carrying only what the playbook check reads."""
    import types

    module = types.ModuleType("stub_personas")
    module.DEFAULT_PERSONA_ID = "only"
    return module


@pytest.mark.parametrize(
    ("hints", "playbooks", "should_fail"),
    [
        ({"urgent": "urgent_path"}, ("urgent_path", "handling_notes"), True),
        (
            {"urgent": "urgent_path", "note": "handling_notes"},
            ("urgent_path", "handling_notes"),
            False,
        ),
        ({}, ("urgent_path",), True),
    ],
    ids=["hoisted-table-misses-a-playbook", "hoisted-table-covers-them", "no-table-at-all"],
)
def test_the_playbook_check_cannot_go_vacuous_when_the_table_moves(
    tmp_path, hints, playbooks, should_fail
):
    """Hoisting the keyword table out of ``select_skills`` must not blind the check.

    The regression it pins: the check read string constants out of
    ``select_skills.__code__``. Moving the table to a module constant — the obvious,
    tidier refactor — emptied ``literals``, emptied ``named``, and the ``if named`` guard
    returned clean. A retargeting agent passed a check that verified nothing, and was
    told nothing. The third case is the other half of the same hole: a selector that can
    never return a playbook at all must fail rather than pass for lack of evidence.
    """
    from aegis.conformance.test_conformance import (
        test_every_playbook_is_reachable_from_select_skills as check,
    )

    spec = _stub_spec(tmp_path, hints, playbooks)
    personas = _personas_stub()
    pieces = {"memory_spec": spec, "personas": personas}

    if should_fail:
        with pytest.raises(BaseException) as excinfo:  # pytest.fail raises Failed
            check(pieces.__getitem__)
        assert "CONFORMANCE FAILURE" in str(excinfo.value)
    else:
        check(pieces.__getitem__)


def test_the_vocabulary_check_fails_on_a_retarget_that_leaks(tmp_path):
    """A changed ``DOMAIN_ID`` plus a shipped-domain word in a core module must fail.

    The proof that the highest-value check actually catches the four defects it was
    written for. The host here is a two-file stand-in for ``backend/src/app``: an adapter
    package declaring a different domain, and one core module beside it that still names
    the shipped domain's persona id — exactly the ``if`` in the login path that made
    every sign-in raise ``KeyError`` after a retarget, and which nothing in the
    repository noticed.
    """
    host = tmp_path / "leaky_host"
    (host / "adapter").mkdir(parents=True)
    (host / "__init__.py").write_text('"""A retargeted host."""\n', encoding="utf-8")
    (host / "adapter" / "__init__.py").write_text(
        '"""The new domain."""\n\n'
        'DOMAIN_ID = "hospital_pharmacy"\n'
        'DOMAIN_DESCRIPTION = "Dispensing, stock and substitution across hospital wards."\n',
        encoding="utf-8",
    )
    (host / "routes.py").write_text(
        '"""The login path, retargeted — except for one line nobody changed."""\n\n'
        "def persona_for(role):\n"
        '    return "pharmacist" if role == "client" else "operations_lead"\n',
        encoding="utf-8",
    )

    env_path = os.pathsep.join([str(_BACKEND_SRC), str(_HERE), str(tmp_path)])
    original = os.environ.get("PYTHONPATH")
    try:
        os.environ["PYTHONPATH"] = env_path
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "--pyargs", "aegis.conformance", "-q",
                "-p", "no:cacheprovider",
                "--aegis-adapter", "leaky_host.adapter",
                "-k", "vocabulary",
            ],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": env_path},
            timeout=300,
            check=False,
        )
    finally:
        if original is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original

    assert result.returncode != 0, result.stdout + result.stderr
    assert "1 failed" in result.stdout
    assert "routes.py:4 names 'operations_lead'" in result.stdout
    assert "shipped-domain string(s) survive outside the adapter" in result.stdout
