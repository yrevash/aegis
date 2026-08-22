#!/usr/bin/env python3
"""Audit probe: snapshot tenant-scoped counts as the SERVING role with scope bound.

Method note: every query runs as aegis_app with SET app.tenant_id bound to a clean
numeric. Never as postgres (superuser bypasses RLS, even FORCE).
"""
import json
import subprocess
import sys

DSN_USER = "aegis_app"
PW = "dqQlz5sFcwhW4GPw2hNUv_sMEdNo_Sm3"
DB = "taif_run1"

TABLES = [
    "runs", "run_events", "usage_ledger", "approvals", "audit_log", "job_runs",
    "redteam_runs", "chunks", "documents", "memory_message", "memory_fact",
    "memory_write_log", "memory_session",
]


def q(sql, tenant="1"):
    """Run SQL as aegis_app with tenant scope bound in the same session."""
    full = f"SET app.tenant_id='{tenant}'; {sql}"
    r = subprocess.run(
        ["psql", "-U", DSN_USER, "-h", "localhost", "-d", DB, "-tAF", "|", "-c", full],
        capture_output=True, text=True, env={"PGPASSWORD": PW, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if r.returncode != 0:
        return f"ERR:{r.stderr.strip()}"
    return r.stdout.strip()


def snapshot(tenant="1"):
    out = {}
    for t in TABLES:
        out[t] = q(f"select count(*) from {t}", tenant)
    # analytics views, tenant-scoped
    out["view_runs_daily"] = q(
        "select coalesce(sum(runs),0) from analytics_runs_daily", tenant)
    out["view_spend_calls"] = q(
        "select coalesce(sum(calls),0) from analytics_spend_daily", tenant)
    out["view_spend_cost"] = q(
        "select coalesce(sum(cost_usd),0) from analytics_spend_daily", tenant)
    out["view_approvals_gates"] = q(
        "select coalesce(sum(gates),0) from analytics_approvals_daily", tenant)
    out["view_audit_events"] = q(
        "select coalesce(sum(events),0) from analytics_audit_daily", tenant)
    out["view_jobs"] = q(
        "select coalesce(sum(jobs),0) from analytics_jobs_daily", tenant)
    out["view_redteam"] = q("select count(*) from analytics_redteam_runs", tenant)
    out["runs_cost_sum"] = q("select coalesce(sum(cost_usd),0) from runs", tenant)
    out["view_runs_cost"] = q(
        "select coalesce(sum(cost_usd),0) from analytics_runs_daily", tenant)
    return out


if __name__ == "__main__":
    tenant = sys.argv[1] if len(sys.argv) > 1 else "1"
    print(json.dumps(snapshot(tenant), indent=2))
