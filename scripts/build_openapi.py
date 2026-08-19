#!/usr/bin/env python3
"""Write ``backend/openapi.json`` — the committed snapshot of the HTTP contract.

Unlike ``scripts/build_api_docs.py``, whose pdoc output is git-ignored, **this artifact
is committed**, and for two reasons that the reference docs do not have:

* it is the **input** to the generated TypeScript client
  (``web/scripts/gen-api-types.mjs``), so the console's build depends on it;
* it is the **snapshot** ``backend/tests/api/test_openapi_snapshot.py`` compares the
  served schema against, so an unreviewed API change fails CI rather than reaching an
  integrator by surprise.

Regenerate it whenever a route, a request model or a ``StreamEvent`` variant changes::

    backend/.venv/bin/python scripts/build_openapi.py

Then regenerate the client, which is one command and produces no diff if nothing moved::

    cd web && npm run gen:api

``--check`` writes nothing and exits non-zero when the file is stale, which is what CI
and the pre-commit hook want.

Keys are sorted on the way out. The order FastAPI emits is deterministic — it follows
route declaration order — but sorting means *moving* a route produces no diff, so a
review only ever sees a change of contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Repo root, from ``scripts/`` → repo.
_ROOT = Path(__file__).resolve().parents[1]

#: The committed document. Beside the backend it describes, not under ``docs/``: it is
#: consumed by two build steps, not read by a person.
SNAPSHOT = _ROOT / "backend" / "openapi.json"


def render() -> str:
    """Return the served OpenAPI document as the exact text of the snapshot file."""
    sys.path.insert(0, str(_ROOT / "backend" / "src"))
    sys.path.insert(0, str(_ROOT / "aegis" / "src"))
    from app.main import app

    return (
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def main() -> int:
    """Write (or check) the snapshot. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed snapshot is stale; write nothing.",
    )
    args = parser.parse_args()

    document = render()
    if args.check:
        current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
        if current == document:
            print(f"{SNAPSHOT} is current.")
            return 0
        print(
            f"{SNAPSHOT} is STALE. Run: backend/.venv/bin/python scripts/build_openapi.py",
            file=sys.stderr,
        )
        return 1
    SNAPSHOT.write_text(document, encoding="utf-8")
    print(f"wrote {SNAPSHOT} ({len(document.splitlines())} lines)")
    return 0


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
