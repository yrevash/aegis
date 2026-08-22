#!/usr/bin/env python3
"""Exercise mutating / parameterised routes with an entitled account and a client."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import call, tok

# (method, path, body, entitled_account)
CASES = [
    ("POST", "/v1/sessions", {"title": "audit probe session"}, "northwind.analyst"),
    ("GET", "/v1/sessions", None, "northwind.analyst"),
    ("POST", "/v1/ml/explain", {"features": {"priority": "high", "category": "billing",
        "channel": "email", "region": "west", "customer_tier": "gold",
        "agent_tenure_months": 12}}, "northwind.analyst"),
    ("POST", "/v1/stack/patch-check", {}, "admin"),
    ("POST", "/v1/database/browse", {"table": "users", "limit": 5}, "admin"),
    ("POST", "/v1/ops/diagnose", {"run_id": "6d23a33bd8064f1f9c3661f5028cf5fc"}, "admin"),
    ("POST", "/v1/reports/tickets", {"format": "csv"}, "admin"),
    ("GET", "/v1/llmops/runs/0fb96449aae349c6a5c19955775896b9", None, "admin"),
    ("GET", "/v1/redteam/runs/nonexistent-run", None, "admin"),
    ("GET", "/v1/settings/agent.agentic_retrieval_max_rounds", None, "admin"),
    ("GET", "/v1/documents/6/ingest", None, "admin"),
    ("POST", "/v1/vision/analyse", {"prompt": "describe", "imageBase64": ""}, "northwind.analyst"),
    ("POST", "/v1/analytics/boards/spend-over-time/data", {}, "northwind.admin"),
    ("POST", "/v1/analytics/boards/spend-over-time/embed-token", {}, "northwind.admin"),
    ("POST", "/v1/mcp/servers/999/test", None, "admin"),
    ("POST", "/v1/jobs/999/requeue", None, "admin"),
    ("POST", "/v1/memory/retention/sweep", {"dryRun": True}, "admin"),
]

if __name__ == "__main__":
    for method, path, body, acct in CASES:
        st, txt = call(method, path, tok(acct), body, timeout=90)
        print(f"[{acct:18}] {method:5} {path:52} {st:4} {' '.join(txt.split())[:200]}")
        # least-privileged client attempt
        st2, txt2 = call(method, path, tok("client"), body, timeout=90)
        print(f"[{'client':18}] {method:5} {path:52} {st2:4} {' '.join(txt2.split())[:140]}")
        print()
