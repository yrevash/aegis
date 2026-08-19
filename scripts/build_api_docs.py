#!/usr/bin/env python3
"""Generate the ``aegis`` API reference with pdoc, into a git-ignored directory.

There is nowhere in this repo to look up a *signature*. ``docs/teaching/`` is a
course and ``docs/module/MODULE_REFERENCE.md`` is the contract; neither is a
reference. pdoc reads the Google-style docstrings ruff already enforces
(``select = [... "D"]``, ``convention = "google"``), so the reference is derived
from the source rather than written beside it and left to rot.

The output is **not committed**. ``docs/api/`` is in ``.gitignore``: generated
docs in git go stale between the commit that changes a signature and the commit
that remembers to rebuild them, and they bury every real diff in review noise.
Rebuild it whenever you want to read it::

    backend/.venv/bin/python scripts/build_api_docs.py
    open docs/api/index.html

``--serve`` runs pdoc's live server instead, which re-reads the source on every
request — the right mode while writing docstrings.

Why an explicit module list: ``aegis/__init__.py`` declares
``__all__ = ["__version__"]``, and pdoc honours ``__all__`` when deciding which
submodules to walk. Pointed at ``aegis`` alone it emits exactly one page. The
subpackages are therefore named individually, read off the filesystem so a new
one is picked up without editing this script.

Appearing in this reference is **not** a stability promise. ``aegis/PUBLIC.md``
is the only place that makes one.

One warning is expected and is not a defect::

    Warn: Found 'TrustworthyModel' in aegis.ml.__all__, but it does not resolve

``aegis.ml`` resolves that name through a module ``__getattr__`` so importing the
package does not drag in xgboost. ``hasattr(aegis.ml, "TrustworthyModel")`` is
True at runtime; pdoc simply cannot attribute a lazily-resolved name to a source
location. Checked before it was written down.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "aegis" / "src"
OUTPUT_DIR = REPO_ROOT / "docs" / "api"


def public_subpackages() -> list[str]:
    """Return every top-level ``aegis.*`` package and module, as dotted names.

    Both kinds are included deliberately. ``aegis.adapter`` is a single module
    rather than a package, and it holds :class:`DomainAdapter` — the one contract
    an integrator implements — so a package-only walk would omit the most
    important page in the reference.

    Returns:
        Sorted dotted names, ``__pycache__`` and any private name excluded.

    Raises:
        FileNotFoundError: If the package source tree is not where it should be.
    """
    root = PACKAGE_ROOT / "aegis"
    if not root.is_dir():
        raise FileNotFoundError(f"aegis package source not found at {root}")
    names: set[str] = set()
    for child in root.iterdir():
        if child.name.startswith(("_", ".")):
            continue
        if child.is_dir() and (child / "__init__.py").is_file():
            names.add(f"aegis.{child.name}")
        elif child.is_file() and child.suffix == ".py":
            names.add(f"aegis.{child.stem}")
    return sorted(names)


def require_pdoc() -> None:
    """Exit with the exact install command if pdoc is missing.

    The same fail-loud contract as :func:`aegis.core.require`: a missing optional
    tool names its own remedy rather than producing a confusing traceback.
    """
    try:
        import pdoc  # noqa: F401  (probe only)
    except ImportError:
        sys.exit(
            "pdoc is not installed in this interpreter.\n"
            f"  uv pip install --python {sys.executable} pdoc\n"
            "Or add the [project.optional-dependencies] docs extra from aegis/pyproject.toml."
        )


def main() -> int:
    """Build (or serve) the reference and report where it landed.

    Returns:
        The pdoc exit code — 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run pdoc's live-reloading server instead of writing files",
    )
    parser.add_argument(
        "modules",
        nargs="*",
        help="dotted module names to document (default: every aegis subpackage)",
    )
    args = parser.parse_args()

    require_pdoc()
    modules = args.modules or public_subpackages()

    command = [sys.executable, "-m", "pdoc"]
    if not args.serve:
        command += ["--output-directory", str(OUTPUT_DIR)]
    command += modules

    env_note = f"PYTHONPATH={PACKAGE_ROOT}"
    print(f"$ {env_note} {' '.join(command)}", file=sys.stderr)

    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{PACKAGE_ROOT}{os.pathsep}{existing}" if existing else str(PACKAGE_ROOT)

    result = subprocess.run(command, env=env, check=False)
    if result.returncode == 0 and not args.serve:
        pages = len(list(OUTPUT_DIR.rglob("*.html")))
        print(f"\nWrote {pages} pages to {OUTPUT_DIR} (git-ignored).", file=sys.stderr)
        print(f"Open {OUTPUT_DIR / 'index.html'}", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
