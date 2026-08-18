"""Importing aegis.jobs pulls the ORM deps and no orchestrator SDK.

The phase contract is that ``aegis.jobs`` declares the record and the host runs the work,
so a consumer who orchestrates differently — or who has no orchestrator at all — can still
import the models, read a tenant's job rows and join them to budgets. The three modules
that make up the contract are all covered: the record (``models``), the stage set
(``stages``) and the isolation guarantee (``scope``). ``scope`` is the one most likely to
grow the forbidden import, because it is the module that most obviously *wants* to know
about activities.

That is only a real constraint while it is checked. ``temporalio`` is installed in this
environment (the host's ``jobs`` extra), so a stray ``import temporalio`` in this package
would work perfectly and be noticed by nobody until someone tried to install ``aegis``
without it. This test is what notices — and the second test is the negative control that
proves the first one can fail, by pointing the identical check at a module that really does
import the SDK.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")

#: What must not appear in ``sys.modules`` after importing the package. ``temporalio`` is
#: the contract; the rest are the host frameworks that would mean ``aegis.jobs`` had
#: started depending on the application composing it.
_BANNED = "{'temporalio', 'fastapi', 'litellm', 'torch', 'langgraph', 'neo4j'}"


def _run_guard(body: str, *, extra_path: str = "") -> subprocess.CompletedProcess[str]:
    """Run an import-guard snippet in a clean interpreter.

    A subprocess rather than an in-process import, because ``sys.modules`` in this one is
    already polluted by every other test in the suite — a guard that ran here would pass
    or fail depending on collection order.

    Args:
        body: Python source to execute.
        extra_path: An additional ``PYTHONPATH`` entry, used by the negative control.

    Returns:
        The finished process, for the caller to assert on.
    """
    path = _SRC if not extra_path else os.pathsep.join((extra_path, _SRC))
    return subprocess.run(
        [sys.executable, "-c", body],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": path},
    )


def test_importing_jobs_pulls_sqlalchemy_but_no_orchestrator_or_host_deps() -> None:
    """Verify aegis.jobs imports the data layer and none of the execution stack."""
    proc = _run_guard(
        "import sys; "
        "import aegis.jobs; "
        "import aegis.jobs.models; "
        "import aegis.jobs.scope; "
        "import aegis.jobs.stages; "
        "assert 'sqlalchemy' in sys.modules, 'expected sqlalchemy (aegis[data])'; "
        # The FK targets: importing the jobs models must be enough for create_all.
        "from aegis.data import AegisBase; "
        "tables = set(AegisBase.metadata.tables); "
        "missing = {'documents', 'job_runs', 'tenants', 'users'} - tables; "
        "assert not missing, missing; "
        # The stage contract must be usable without the SDK — that is the whole claim.
        "from aegis.jobs.stages import remaining_stages; "
        "assert [s.name for s in remaining_stages('enrich')] == ['embed','index','graph']; "
        f"banned = {_BANNED}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_guard_fails_when_the_orchestrator_is_actually_imported(tmp_path) -> None:
    """The negative control: the same check, pointed at a module that does import it.

    Without this, a guard that had quietly stopped checking anything — a renamed module,
    an ``assert`` inside a string that never runs — would keep passing forever. Here the
    identical assertion is run against a package that imports ``temporalio`` on purpose,
    and the test demands a **failure**.
    """
    package = tmp_path / "not_aegis_jobs"
    package.mkdir()
    (package / "__init__.py").write_text("import temporalio  # noqa: F401\n")

    proc = _run_guard(
        "import sys; "
        "import not_aegis_jobs; "
        f"banned = {_BANNED}; "
        "hit = banned & set(sys.modules); "
        "assert not hit, hit",
        extra_path=str(tmp_path),
    )

    assert proc.returncode != 0, (
        "the import guard passed on a module that imports temporalio, so it cannot "
        "detect the import it exists to forbid"
    )
    assert "temporalio" in proc.stderr
