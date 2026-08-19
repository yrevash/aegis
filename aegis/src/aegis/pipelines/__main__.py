"""``python -m aegis.pipelines`` — write the pipeline reference to stdout.

Verifies the declarations against the code first, so a document can never be generated
from a spec the runtime already contradicts.
"""

from __future__ import annotations

import sys

from aegis.pipelines.bindings import verify_pipelines
from aegis.pipelines.docs import render_markdown


def main() -> int:
    """Verify the declarations and print the Markdown reference.

    Returns:
        ``0``. A drift raises :class:`~aegis.pipelines.bindings.PipelineDriftError`
        rather than returning a non-zero code, so the message names the difference.
    """
    verify_pipelines()
    sys.stdout.write(render_markdown())
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
