"""Importing aegis.jobs pulls the ORM deps and no orchestrator SDK.

The phase contract is that ``aegis.jobs`` declares the record and the host runs the work,
so a consumer who orchestrates differently — or who has no orchestrator at all — can still
import the models, read a tenant's job rows and join them to budgets.

That is only a real constraint while it is checked. ``temporalio`` is installed in this
environment (the host's ``jobs`` extra), so a stray ``import temporalio`` in this package
would work perfectly and be noticed by nobody until someone tried to install ``aegis``
without it. This test is what notices.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_jobs_pulls_sqlalchemy_but_no_orchestrator_or_host_deps() -> None:
    """Verify aegis.jobs imports the data layer and none of the execution stack."""
    code = (
        "import sys; "
        "import aegis.jobs; "
        "import aegis.jobs.models; "
        "assert 'sqlalchemy' in sys.modules, 'expected sqlalchemy (aegis[data])'; "
        # The FK targets: importing the jobs models must be enough for create_all.
        "from aegis.data import AegisBase; "
        "tables = set(AegisBase.metadata.tables); "
        "missing = {'documents', 'job_runs', 'tenants', 'users'} - tables; "
        "assert not missing, missing; "
        "banned = {'temporalio', 'fastapi', 'litellm', 'torch', 'langgraph', 'neo4j'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
