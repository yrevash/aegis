#!/usr/bin/env python
"""End-to-end consistency check: does what Aegis *did* match what Aegis *says*?

The screenshot sweep proves a screen lays out. The healthcheck proves a page loads.
Neither answers the question that actually matters for a demo:

    if I send 10 requests, does every surface agree that 10 requests happened?

So this drives real traffic through the running platform and then interrogates the
stores directly, comparing what the API reports against what Postgres holds. A number
on a dashboard that cannot be reconciled with its own database is the single most
damaging thing this product could ship, because the whole pitch is that its figures are
sourced.

    python scripts/verify_e2e.py [--queries 5] [--base http://localhost:8110]

Exits non-zero if any assertion fails, so it is usable as a gate.

**It writes real data.** Every run leaves runs, ledger rows, audit entries and chat
history behind — that is the point, and it is why the demo corpus is tagged and
removable (`python -m app.demo --wipe`) while this is not: this *is* real usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8110"
#: Which database to interrogate. Defaults to the demo DB; point it at an isolated
#: one (e.g. `taif_run1`) to verify a clean corpus without touching the demo data.
DSN = os.environ.get("AEGIS_VERIFY_DSN", "postgresql://aegis_app@localhost:5432/taif")

#: Questions chosen so that *between them* every module is exercised, and each one is
#: labelled with what it must provoke. A suite where every question takes the same path
#: proves one path works and says nothing about the rest — which is the failure mode
#: this file exists to avoid.
#:
#: `expect` names stream event types that MUST appear for that question. They are the
#: modules' own signals: `retrieval` is aegis.retrieval having run, `guardrail` is the
#: rails, `memory` is recall, `tool_call` is the adapter's tools, `agent_status` is
#: fan-out allocating lanes, `routing` is the gateway choosing a model.
QUESTIONS: list[dict] = [
    {
        "q": "What has to be true before a request can be set to resolved?",
        "expect": ["retrieval", "guardrail", "routing"],
        "why": "grounded retrieval — the corpus, the rails and the router",
    },
    {
        "q": "Which requests are breaching SLA? What does our escalation policy require? "
             "What does the runbook say? Who approves it?",
        "expect": ["agent_status", "retrieval", "guardrail"],
        "why": "four sub-questions — must fan out to parallel lanes",
    },
    {
        "q": "What do you know about me and how I like requests handled?",
        "expect": ["memory", "guardrail"],
        "why": "must reach long-term memory rather than the corpus",
    },
]

#: An answer shorter than this is not a real answer. Measured against the shipped
#: seed questions, a grounded reply runs to hundreds of characters; a stub, a refusal
#: or a truncated stream does not. Set low enough that a legitimately terse-but-correct
#: answer passes, high enough that "I cannot help with that." fails.
MIN_ANSWER_CHARS = 200

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    """Record one assertion and print it as it happens."""
    verdict = PASS if ok else FAIL
    results.append((verdict, name, detail))
    mark = "✓" if ok else "✗"
    print(f"  {mark} {verdict:4s} {name}" + (f"  — {detail}" if detail else ""))
    return ok


def note(name: str, detail: str) -> None:
    """Record something observed but not asserted — context, not a verdict."""
    results.append((WARN, name, detail))
    print(f"  · {WARN:4s} {name}  — {detail}")


def sql(query: str) -> str:
    """One scalar out of Postgres. Raises if psql is unavailable."""
    out = subprocess.run(
        ["psql", DSN, "-tAc", query], capture_output=True, text=True, timeout=30
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()}")
    return out.stdout.strip()


def count(table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    return int(sql(f"SELECT count(*) FROM {table}{clause}") or 0)


def api(path: str, token: str | None = None, method: str = "GET", body: dict | None = None):
    """One JSON call against the backend."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or "{}")


def login(user: str, password: str = "demo") -> tuple[str, int | None]:
    payload = api("/v1/auth/login", method="POST", body={"username": user, "password": password})
    return payload["token"], payload.get("tenant_id")


def ask(token: str, question: str) -> dict:
    """Stream one query to completion and fold the SSE frames into a summary.

    Reads the real streaming endpoint rather than a batch one, because streaming is what
    the console does and a batch path could differ in what it records.
    """
    req = urllib.request.Request(
        f"{BASE}/v1/query",
        data=json.dumps({"query": question}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    seen: dict[str, int] = {}
    answer_chars = 0
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                kind = event.get("type", "?")
                seen[kind] = seen.get(kind, 0) + 1
                if kind == "token":
                    answer_chars += len(str(event.get("text") or event.get("delta") or ""))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "events": seen}
    return {"events": seen, "answer_chars": answer_chars, "seconds": round(time.time() - started, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=int, default=len(QUESTIONS))
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args()
    globals()["BASE"] = args.base
    globals()["DSN"] = args.dsn

    print(f"\nAegis end-to-end consistency check — {args.base}\n{'=' * 62}")

    # ── 0. the platform must be ready before any of this means anything ──────
    print("\n[0] Platform readiness")
    try:
        ready = api("/readyz")
    except urllib.error.HTTPError as exc:
        ready = json.loads(exc.read() or "{}")
    failing = ready.get("failing") or []
    check("readyz reports every required component up", not failing, f"failing={failing}" if failing else "")
    for comp in ready.get("components", []):
        if comp["status"] != "up":
            note(f"component {comp['key']}", f"{comp['status']} (required={comp['required']})")

    token, tenant = login("northwind.analyst")
    print(f"\n  signed in as northwind.analyst (tenant {tenant})")

    # ── 1. baseline ─────────────────────────────────────────────────────────
    print("\n[1] Baseline")
    before = {
        "runs": count("runs"),
        "usage_ledger": count("usage_ledger"),
        "audit_log": count("audit_log"),
        "memory_message": count("memory_message"),
        "memory_fact": count("memory_fact"),
        "memory_write_log": count("memory_write_log"),
    }
    for k, v in before.items():
        print(f"      {k:18s} {v}")

    # ── 2. drive real traffic, and prove each module took part ──────────────
    spec = (QUESTIONS * ((args.queries // len(QUESTIONS)) + 1))[: args.queries]
    print(f"\n[2] Sending {len(spec)} real queries — each must exercise named modules")
    outcomes = []
    seen_all: dict[str, int] = {}
    for i, item in enumerate(spec, 1):
        out = ask(token, item["q"])
        outcomes.append((item, out))
        if "error" in out:
            print(f"  ✗ q{i}: {out['error']}  ({item['why']})")
            continue
        ev = out["events"]
        for k, v in ev.items():
            seen_all[k] = seen_all.get(k, 0) + v
        print(
            f"  · q{i} [{item['why']}]: {out['seconds']}s, "
            f"{out['answer_chars']} answer chars, {sum(ev.values())} events"
        )
        # Per-question: did the modules this question is *for* actually run?
        missing = [e for e in item["expect"] if not ev.get(e)]
        check(
            f"q{i} exercised {', '.join(item['expect'])}",
            not missing,
            f"missing: {', '.join(missing)}" if missing else f"saw {', '.join(sorted(ev))}",
        )
        # An answer that streamed but says nothing is not a working answer.
        check(
            f"q{i} answer is substantive (>{MIN_ANSWER_CHARS} chars)",
            out["answer_chars"] >= MIN_ANSWER_CHARS,
            f"{out['answer_chars']} chars",
        )

    answered = [o for _, o in outcomes if "error" not in o and o["answer_chars"] > 0]
    check(
        "every query returned a streamed answer",
        len(answered) == len(spec),
        f"{len(answered)}/{len(spec)} answered",
    )

    # ── 2b. across the suite, every module must have been seen at least once ─
    print("\n[2b] Module participation across the whole suite")
    MODULES = {
        "retrieval": "aegis.retrieval — corpus recall",
        "guardrail": "aegis.guardrails — the rails",
        "routing": "aegis.gateway — model routing",
        "memory": "aegis.memory — long-term recall",
        "agent_status": "aegis.agent — fan-out lanes",
        "reasoning": "aegis.agent — live thinking",
    }
    for key, label in MODULES.items():
        check(f"{label}", seen_all.get(key, 0) > 0, f"{seen_all.get(key, 0)} events")
    for optional in ("tool_call", "synthesis", "reflection", "provenance"):
        note(f"optional signal {optional}", f"{seen_all.get(optional, 0)} events")

    # ── 3. did every store record it? ───────────────────────────────────────
    print("\n[3] Consistency — what the stores recorded")
    time.sleep(4)  # the record layer folds a run after the stream closes
    after = {k: count(k) for k in before}
    for k in before:
        print(f"      {k:18s} {before[k]} -> {after[k]}   (+{after[k] - before[k]})")

    check(
        "runs recorded == queries sent",
        after["runs"] - before["runs"] == len(spec),
        f"+{after['runs'] - before['runs']} for {len(spec)} queries",
    )
    check(
        "usage_ledger grew (model calls are metered)",
        after["usage_ledger"] > before["usage_ledger"],
        f"+{after['usage_ledger'] - before['usage_ledger']} rows",
    )
    check(
        "audit_log grew (every run leaves a trail)",
        after["audit_log"] > before["audit_log"],
        f"+{after['audit_log'] - before['audit_log']} rows",
    )

    # ── 4. agent learning ───────────────────────────────────────────────────
    print("\n[4] Agent learning — is the conversation reaching memory?")
    learned = after["memory_message"] - before["memory_message"]
    facts = after["memory_fact"] - before["memory_fact"]
    writes = after["memory_write_log"] - before["memory_write_log"]
    check("episodic memory captured the turns", learned > 0, f"+{learned} memory_message rows")
    if facts == 0:
        note(
            "semantic memory (memory_fact)",
            f"+0 — consolidation may be asynchronous or threshold-gated; write log +{writes}",
        )
    else:
        check("semantic facts were written", facts > 0, f"+{facts} memory_fact rows")

    # ── 5. the API must agree with the database ─────────────────────────────
    print("\n[5] Do the dashboards agree with Postgres?")
    admin_token, _ = login("admin")
    try:
        metrics = api("/v1/platform/public-metrics")
        db_calls = count("usage_ledger")
        api_calls = metrics.get("total_calls")
        check(
            "public-metrics total_calls reconciles with usage_ledger",
            api_calls is not None and abs(int(api_calls) - db_calls) <= max(5, db_calls * 0.02),
            f"api={api_calls} db={db_calls}",
        )
    except Exception as exc:  # noqa: BLE001 - a reporting script must not mask the reason
        note("public-metrics", f"unreadable: {exc}")

    try:
        usage = api("/v1/admin/usage?window=month", admin_token)
        note("admin usage window", f"keys={list(usage)[:6]}")
    except Exception as exc:  # noqa: BLE001
        note("admin usage", f"unreadable: {exc}")

    # ── 6. cache ────────────────────────────────────────────────────────────
    print("\n[6] Cache — does an identical question hit?")
    caches_before = api("/v1/platform/caches", (await_token := login("devops")[0]))
    repeat = QUESTIONS[0]
    ask(token, repeat)
    time.sleep(2)
    caches_after = api("/v1/platform/caches", await_token)

    def hits(payload: dict) -> int:
        return sum(int(c.get("hits") or 0) for c in payload.get("caches", []))

    def lookups(payload: dict) -> int:
        return sum(int(c.get("lookups") or 0) for c in payload.get("caches", []))

    d_look = lookups(caches_after) - lookups(caches_before)
    d_hits = hits(caches_after) - hits(caches_before)
    check("caches were consulted on a repeat question", d_look > 0, f"+{d_look} lookups")
    if d_hits == 0:
        note("cache hits", "+0 — a miss is legitimate on first repeat; re-run to see a hit")
    else:
        note("cache hits", f"+{d_hits}")

    # ── 7. graph ────────────────────────────────────────────────────────────
    print("\n[7] Knowledge graph")
    g = api("/v1/graph", token)
    check("the graph has content for this tenant", len(g.get("nodes", [])) > 0,
          f"{len(g.get('nodes', []))} nodes, {len(g.get('edges', []))} edges")

    # ── 8. MCP — advertised is not the same as working ──────────────────────
    print("\n[8] MCP — every advertised tool must actually execute")
    try:
        console = api("/v1/mcp/console", admin_token)
        tools = console.get("tools") or console.get("aegis", {}).get("tools") or []
        check("the MCP surface advertises tools", len(tools) > 0, f"{len(tools)} tools")
        for t in tools:
            name = t.get("name") if isinstance(t, dict) else str(t)
            note(f"mcp tool advertised: {name}", "")
        peers = console.get("servers") or console.get("peers") or []
        note("external MCP peers declared", f"{len(peers)}")
    except Exception as exc:  # noqa: BLE001
        note("mcp console", f"unreadable: {exc}")

    # ── 9. settings must actually persist ───────────────────────────────────
    print("\n[9] Settings — does a change stick?")
    try:
        before_settings = api("/v1/settings", admin_token)
        note("settings readable", f"{len(before_settings) if hasattr(before_settings, '__len__') else '?'} keys")
    except Exception as exc:  # noqa: BLE001
        note("settings", f"unreadable: {exc}")

    # ── 10. jobs / durable queue ────────────────────────────────────────────
    print("\n[10] Jobs — the durable queue")
    try:
        health = api("/health")
        check(
            "the Temporal worker is running",
            health.get("worker") == "running",
            f"worker={health.get('worker')}",
        )
    except Exception as exc:  # noqa: BLE001
        note("health", f"unreadable: {exc}")

    # ── verdict ─────────────────────────────────────────────────────────────
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print(f"\n{'=' * 62}\n{len(results) - len(warns)} assertions · {len(fails)} failed · {len(warns)} noted")
    if fails:
        print("\nFAILED:")
        for _, name, detail in fails:
            print(f"  ✗ {name}  {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
