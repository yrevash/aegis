"""The conformance suite is proved the only way that means anything: by running it.

Two claims, and neither is credible from reading the checks:

* ``pytest --pyargs aegis.conformance`` **passes** against the reference adapter, and
* it **fails, specifically**, against a deliberately mis-wired one — ``broken_adapter/``
  next door, where every break is a plausible first attempt rather than a caricature.

Twelve of the thirteen checks fail against that fixture and the thirteenth passes, which
is the part that stops this being a tautology: a check that cannot fail is decoration,
and a suite that fails everything the moment anything is wrong tells an integrator
nothing about where to look.

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

#: The one check ``broken_adapter`` does not break: its ``skills/`` directory is intact.
EXPECTED_PASS = "test_skills_directory_holds_at_least_one_playbook"


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
    assert "13 passed" in result.stdout


def test_a_mis_wired_adapter_fails_check_by_check(broken_run):
    result = broken_run
    assert result.returncode != 0
    failed = {
        line.rsplit("::", 1)[-1].split(" - ")[0].strip()
        for line in result.stdout.splitlines()
        if line.startswith("FAILED ")
    }
    assert failed == set(EXPECTED_FAILURES), sorted(failed)
    assert EXPECTED_PASS not in failed
    assert "12 failed, 1 passed" in result.stdout


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
