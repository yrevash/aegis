#!/usr/bin/env python3
"""Audit probe: sweep GET routes across accounts. Read-only."""
import json, sys, urllib.request, urllib.error, os

BASE = "http://127.0.0.1:8110"
AUD = os.path.dirname(os.path.abspath(__file__))

def tok(u):
    p = os.path.join(AUD, f"tok_{u}.txt")
    return open(p).read().strip() if os.path.exists(p) else None

def call(method, path, token=None, body=None, timeout=45):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"EXC {type(e).__name__}: {e}"

def summarize(txt, n=220):
    t = " ".join(txt.split())
    return t[:n]

if __name__ == "__main__":
    spec = json.load(open("/Users/yrevash/aegis/backend/openapi.json"))
    accounts = sys.argv[1].split(",") if len(sys.argv) > 1 else ["admin"]
    gets = []
    for p, ops in sorted(spec["paths"].items()):
        o = ops.get("get")
        if not o:
            continue
        if "{" in p:
            continue
        reqp = [x for x in o.get("parameters", []) if x.get("required")]
        if reqp:
            continue
        gets.append(p)
    out = {}
    for acct in accounts:
        t = tok(acct) if acct != "anon" else None
        for p in gets:
            st, txt = call("GET", p, t)
            out.setdefault(p, {})[acct] = (st, len(txt), summarize(txt))
            print(f"{acct:18} GET {p:42} {st:4} len={len(txt):7} {summarize(txt,140)}", flush=True)
    json.dump(out, open(os.path.join(AUD, "sweep_result.json"), "w"), indent=1)
