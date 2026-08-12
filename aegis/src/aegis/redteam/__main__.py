"""CLI over the importable harness: ``python -m aegis.redteam``.

Runs the curated battery through the real guardrail rail (offline — deterministic
backstops only, no API key) and prints the report. ``--json`` emits the lossless
:meth:`RedTeamReport.as_dict` projection; ``--min-block-rate`` sets the pass bar.
Exit code is non-zero when the run fails its threshold, so it can gate CI.

This is the offline, in-process source of truth. ``backend/scripts/garak_scan.py``
remains the complementary *live-LLM* scan (garak against the real endpoint) run on
the day for the "base vs guarded" baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from aegis.redteam.runner import RedTeamThresholds, run_redteam


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="aegis.redteam", description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit the report as JSON (as_dict projection)"
    )
    parser.add_argument(
        "--min-block-rate",
        type=float,
        default=RedTeamThresholds().min_block_rate,
        help="minimum attack block rate to pass (0..1)",
    )
    parser.add_argument(
        "--max-false-positive-rate",
        type=float,
        default=RedTeamThresholds().max_false_positive_rate,
        help="maximum benign-control false-positive rate to pass (0..1)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the offline red-team and print the report; return a shell exit code."""
    args = _parse_args(argv)
    thresholds = RedTeamThresholds(
        min_block_rate=args.min_block_rate,
        max_false_positive_rate=args.max_false_positive_rate,
    )
    report = asyncio.run(run_redteam(thresholds=thresholds))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(report.summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
