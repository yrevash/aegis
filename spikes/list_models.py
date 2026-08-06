"""
Enumerate the models actually available on the TCS GenAI Lab gateway.

Ground-truth for the heterogeneous routing table, the embeddings model, and the
reasoning model used by the LLM-as-judge eval. Do not build routing from memory —
run this and use what it returns.

Run:  python spikes/list_models.py
"""

import httpx

BASE_URL = "https://genailab.tcs.in"
API_KEY = "PASTE_YOUR_KEY_HERE"  # same key that worked for the chat call

client = httpx.Client(verify=False)  # self-signed cert on the gateway

# OpenAI-compatible gateways usually expose GET /models (some use /v1/models).
for path in ("/models", "/v1/models"):
    try:
        r = client.get(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=30,
        )
        print(f"GET {path} -> {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            models = data.get("data", data)
            for m in models:
                print("  -", m.get("id", m) if isinstance(m, dict) else m)
            break
    except Exception as e:  # noqa: BLE001
        print(f"GET {path} failed: {e}")
