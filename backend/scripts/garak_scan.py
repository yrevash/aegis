#!/usr/bin/env python3
"""garak_scan.py — the real LLM red-team / vulnerability scan, run **on the day**.

This is a *runner*, not a stored result. It drives NVIDIA's `garak`
(https://github.com/NVIDIA/garak) against our system's live LLM surface with a
curated adversarial probe set, and writes garak's own report into
``scripts/garak_reports/`` (gitignored). Nothing here fabricates a scan: if
``garak`` isn't installed, or the gateway key / network / backend isn't there,
the script prints clear install+run instructions and exits non-zero **without
writing any report**.

Why deferred: garak needs the gateway API key + network egress (or a running
backend), which are only available on the hackathon machine on the day. The
prerequisites are exactly those already required to run the platform live
(``docs/RUNBOOK.md`` "Lite" rung), so this adds no new day-of dependency beyond
``pip install garak`` itself. See ``docs/security.md`` §3.

Two targets (choose with ``--target``):

* ``gateway`` (default) — point garak's stable ``rest`` generator at the same
  upstream OpenAI-compatible gateway + model our LiteLLM chokepoint uses
  (``genailab.tcs.in``). This measures the **base model's** raw vulnerability —
  the number our layered guardrails then mitigate. Needs ``GENAILAB_API_KEY`` +
  network. This is the honest "before guardrails" baseline.

* ``endpoint`` — point garak at our **guardrail-protected** ``POST /query`` SSE
  endpoint (via ``--api-base``, default ``http://localhost:8000``). The runner
  logs in first to mint a JWT and injects it. garak scans the streamed event
  body, so a blocked probe shows up as a guardrail/refusal event rather than a
  compliant answer — the "after guardrails" surface. Needs a running backend.

Run both and compare block rates to tell the "base vs guarded" story.

Curated probes (``--probes``; garak module names): ``promptinject`` (goal
hijacking / instruction override), ``dan`` (jailbreak / DAN personas),
``encoding`` (obfuscated-payload injection), ``leakreplay`` (training-data /
system-prompt leakage). Override with ``--probes a,b,c``.

Usage (on the day):

    pip install garak                     # dev/day-of tool — NOT a core runtime dep
    export GENAILAB_API_KEY=...           # the gateway key (see backend/.env)
    python scripts/garak_scan.py --target gateway
    # or, against the live guarded endpoint:
    #   uvicorn app.main:app --app-dir src        # backend on :8000
    #   python scripts/garak_scan.py --target endpoint

    python scripts/garak_scan.py --dry-run        # preflight only; prints readiness

Only stdlib is imported at module load, so this parses and prints instructions
even when neither garak nor the app package is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlparse

# The curated, security.md-aligned default probe set (garak module names).
DEFAULT_PROBES = "promptinject,dan,encoding,leakreplay"

# Gateway defaults mirror ``backend/src/app/config.py`` / ``core/models.py`` so
# the scan hits the *same* upstream the platform actually uses. Env wins.
DEFAULT_BASE_URL = os.environ.get("GENAILAB_BASE_URL", "https://genailab.tcs.in")
DEFAULT_MODEL = os.environ.get("MODEL_CHEAP", "genailab-maas-gpt-4o-mini")
DEFAULT_API_BASE = os.environ.get("TAIF_API_BASE", "http://localhost:8000")

# Reports land next to this script, in a gitignored directory.
REPORTS_DIR = Path(__file__).resolve().parent / "garak_reports"


def _garak_available() -> bool:
    """Return whether garak is runnable (on PATH or importable in this env)."""
    return shutil.which("garak") is not None or find_spec("garak") is not None


def _garak_argv0() -> list[str]:
    """Return the argv prefix that launches garak (console script or ``-m``)."""
    exe = shutil.which("garak")
    if exe is not None:
        return [exe]
    # No console script but the package is importable — run it as a module.
    return [sys.executable, "-m", "garak"]


def _host_port(url: str) -> tuple[str, int]:
    """Return ``(host, port)`` for a URL, defaulting the port from the scheme."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _tcp_reachable(url: str, timeout: float = 4.0) -> bool:
    """Return whether a TCP connection to the URL's host:port succeeds quickly."""
    host, port = _host_port(url)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _login_for_token(api_base: str, username: str, password: str) -> str | None:
    """Log in to the backend and return a JWT, or ``None`` on any failure.

    Uses only stdlib ``urllib`` so the runner has no third-party import at load.
    """
    url = api_base.rstrip("/") + "/auth/login"
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:  # noqa: S310 - local
            payload = json.loads(resp.read().decode())
        token = payload.get("token")
        return token if isinstance(token, str) and token else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _instructions(target: str, problems: list[str]) -> str:
    """Build the honest not-ready message (install + run steps). No report written."""
    lines = [
        "",
        "=" * 72,
        "  GARAK RED-TEAM RUNNER — NOT READY (no scan run, no report written)",
        "=" * 72,
        "  Preconditions not met:",
    ]
    lines += [f"    - {p}" for p in problems]
    lines += [
        "",
        "  To run this for real on the day:",
        "    1. Install the scanner (dev/day-of tool — NOT a core runtime dep):",
        "         pip install garak",
    ]
    if target == "gateway":
        lines += [
            "    2. Export the gateway key (see backend/.env):",
            "         export GENAILAB_API_KEY=...      # Windows: setx GENAILAB_API_KEY ...",
            "    3. Ensure network egress to the gateway"
            f" ({DEFAULT_BASE_URL}).",
            "    4. Re-run:",
            "         python scripts/garak_scan.py --target gateway",
        ]
    else:
        lines += [
            "    2. Start the backend (RUNBOOK 'Lite' rung is enough):",
            "         cd backend && uvicorn app.main:app --app-dir src   # :8000",
            "    3. Make sure GENAILAB_API_KEY is set so /query can reach the model.",
            "    4. Re-run:",
            "         python scripts/garak_scan.py --target endpoint",
        ]
    lines += [
        "",
        "  Reports are written to scripts/garak_reports/ (gitignored) — commit",
        "  the real .report.jsonl / .report.html there once the scan completes.",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def _preflight(args: argparse.Namespace) -> tuple[list[str], dict | None]:
    """Check readiness and (when ready) build the garak generator config.

    Returns ``(problems, config)``. If ``problems`` is non-empty the caller must
    print instructions and exit non-zero **without** running garak or writing a
    report. ``config`` is the garak ``--generator_option_file`` dict when ready.
    """
    problems: list[str] = []
    config: dict | None = None

    if not _garak_available():
        problems.append("garak is not installed (pip install garak).")

    if args.target == "gateway":
        api_key = os.environ.get("GENAILAB_API_KEY", "").strip()
        if not api_key:
            problems.append("GENAILAB_API_KEY is not set (the gateway API key).")
        if not _tcp_reachable(args.base_url):
            problems.append(
                f"gateway {args.base_url} is not reachable (no network egress?)."
            )
        if not problems:
            config = _gateway_config(args, api_key)
    else:  # endpoint
        if not _tcp_reachable(args.api_base):
            problems.append(
                f"backend {args.api_base} is not reachable "
                "(start it: uvicorn app.main:app --app-dir src)."
            )
            token = None
        else:
            token = _login_for_token(args.api_base, args.username, args.password)
            if token is None:
                problems.append(
                    f"could not log in at {args.api_base}/auth/login as "
                    f"{args.username!r} (backend up but auth failed?)."
                )
        if not problems and token is not None:
            config = _endpoint_config(args, token)

    return problems, config


def _gateway_config(args: argparse.Namespace, api_key: str) -> dict:
    """garak ``rest`` generator config for the upstream OpenAI-compatible gateway.

    Targets ``<base_url>/v1/chat/completions`` with the same model role the
    platform routes to, measuring the base model's raw susceptibility.
    """
    return {
        "rest": {
            "RestGenerator": {
                "name": f"genailab:{args.model}",
                "uri": args.base_url.rstrip("/") + "/v1/chat/completions",
                "method": "post",
                "headers": {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                "req_template_json_object": {
                    "model": args.model,
                    "messages": [{"role": "user", "content": "$INPUT"}],
                    "temperature": 0,
                },
                "response_json": True,
                "response_json_field": "$.choices[0].message.content",
                # The gateway uses a self-signed cert (config.genailab_ssl_verify=False).
                "verify_ssl": args.verify_ssl,
                "request_timeout": 60,
            }
        }
    }


def _endpoint_config(args: argparse.Namespace, token: str) -> dict:
    """garak ``rest`` generator config for our guardrail-protected ``/query`` SSE.

    garak scans the streamed event body as the model output; a blocked probe
    surfaces as a guardrail/refusal event rather than a compliant answer, so this
    measures the *guarded* surface (compare its block rate against ``gateway``).
    """
    return {
        "rest": {
            "RestGenerator": {
                "name": "taif-guarded-query",
                "uri": args.api_base.rstrip("/") + "/query",
                "method": "post",
                "headers": {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                "req_template_json_object": {"query": "$INPUT"},
                # SSE stream: take the raw event body as the output to scan.
                "response_json": False,
                "request_timeout": 120,
            }
        }
    }


def _run_garak(config: dict, args: argparse.Namespace) -> int:
    """Write the (secret-bearing) generator config, then exec garak. Returns rc."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_prefix = REPORTS_DIR / f"garak_{args.target}_{stamp}"

    # The config carries a bearer token / API key, so keep it inside the
    # gitignored reports dir and remove it after the run.
    cfg_path = REPORTS_DIR / f".generator_{args.target}_{stamp}.json"
    cfg_path.write_text(json.dumps(config, indent=2))

    argv = [
        *_garak_argv0(),
        "--model_type",
        "rest",
        "--generator_option_file",
        str(cfg_path),
        "--probes",
        args.probes,
        "--report_prefix",
        str(report_prefix),
    ]

    print(f"[garak_scan] target={args.target} probes={args.probes}")
    print(f"[garak_scan] report prefix: {report_prefix}")
    print(f"[garak_scan] $ {' '.join(argv)}")
    try:
        completed = subprocess.run(argv, check=False)  # noqa: S603 - trusted argv
        return completed.returncode
    finally:
        # Never leave the token/key-bearing config on disk.
        try:
            cfg_path.unlink()
        except OSError:
            pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="garak_scan.py",
        description=(
            "Run garak (LLM red-team / vulnerability scanner) against our LLM "
            "surface. Honest-degrades (exit non-zero, no report) if garak / key "
            "/ network / backend is unavailable."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target",
        choices=("gateway", "endpoint"),
        default="gateway",
        help="gateway = base model via the OpenAI-compatible gateway (default); "
        "endpoint = our guardrail-protected /query SSE surface.",
    )
    parser.add_argument(
        "--probes",
        default=DEFAULT_PROBES,
        help=f"comma-separated garak probe modules (default: {DEFAULT_PROBES}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"gateway deployment id to probe (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--base-url",
        dest="base_url",
        default=DEFAULT_BASE_URL,
        help=f"gateway base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--api-base",
        dest="api_base",
        default=DEFAULT_API_BASE,
        help=f"backend base URL for --target endpoint (default: {DEFAULT_API_BASE}).",
    )
    parser.add_argument(
        "--username", default="admin", help="login user for --target endpoint."
    )
    parser.add_argument(
        "--password", default="admin", help="login password for --target endpoint."
    )
    parser.add_argument(
        "--verify-ssl",
        dest="verify_ssl",
        action="store_true",
        help="verify the gateway TLS cert (off by default — self-signed gateway).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run preflight only; print readiness and exit (no scan).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code (0 ok, non-zero not-run/failed)."""
    args = _parse_args(argv)
    problems, config = _preflight(args)

    if problems:
        # Honest degrade: no scan, no report, clear instructions, non-zero exit.
        print(_instructions(args.target, problems))
        return 2

    if args.dry_run:
        print(
            f"[garak_scan] READY — target={args.target}, probes={args.probes}. "
            "Re-run without --dry-run to execute the scan."
        )
        return 0

    assert config is not None  # guaranteed by _preflight when no problems
    rc = _run_garak(config, args)
    if rc == 0:
        print(
            f"[garak_scan] done — reports in {REPORTS_DIR} (gitignored). "
            "Commit the .report.* files as the real red-team artifact."
        )
    else:
        print(f"[garak_scan] garak exited {rc}; see output above.", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
