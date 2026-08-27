# Phase 6 — AUDIT GATE (A2A protocol)

Commit under audit: `64072d8` — *feat(a2a): Aegis speaks Agent2Agent, and the routing tenant cannot reach the database*
Branch: `docs/wow-pass-plan` · Audited: 2026-08-27 · Live server: `http://127.0.0.1:8110` (running process, kid `6H2MIH2_B7TyBryGlFYHKQ`)

**Evidence marks.** **[MEASURED]** — run on this machine, output pasted. **[SOURCE]** — read in the code, with `file:line`. **[DOC]** — the specification, with URL and line.

Spec sources used, fetched fresh:
- `https://raw.githubusercontent.com/a2aproject/A2A/main/docs/specification.md` (3620 lines) **[DOC]**
- `https://raw.githubusercontent.com/a2aproject/A2A/main/specification/a2a.proto` (812 lines) — the normative data model that `specification.md` renders via `proto_to_table()` **[DOC]**
- RFC 8785 reference implementation: `rfc8785==0.1.4` (PyPI), used as the independent JCS oracle **[MEASURED]**

---

## VERDICT: **FAIL**

Three independent reasons, any one of which is sufficient:

1. **The surface is functionally inert.** No A2A peer can ever obtain an answer from Aegis. `SendMessage` returns `TASK_STATE_SUBMITTED` and runs nothing; `GetTask` always returns an error; `SendStreamingMessage` is not implemented. There is no path from a peer's question to an Aegis answer. (F-01)
2. **An attacker-controlled HTTP header rewrites the signed Agent Card**, including the `jku` inside the signed protected header, so Aegis's own key signs a card that points peers at the attacker's origin — served with `Cache-Control: public, max-age=300`. (F-02)
3. **The wire format diverges from A2A 1.0 in five places**, one of which (`protocolVersion` at card top level) provably breaks signature verification for any spec-conformant verifier. (F-04 … F-08)

Plus: any non-string `tenant` returns **HTTP 500** (F-03), the route slipped past `test_route_coverage.py` entirely (F-09), the canonicaliser is **not** RFC 8785 (F-10), and the commit message describes an unsigned-card code path **that does not exist** (F-13).

What holds: the signature itself is genuine — it verifies against the published JWKS, and a tampered card does not verify **[MEASURED]**. The `tenant` routing string never touches `app.tenant_id` **[SOURCE]**. The non-ASCII-digit hole the commit describes closing is genuinely closed **[MEASURED]**.

---

## Severity index

| # | Severity | Finding |
|---|---|---|
| F-01 | **CRITICAL** | The A2A surface cannot return an answer to anyone, ever |
| F-02 | **CRITICAL** | `Host:` header rewrites the *signed* card and its `jku`; no config escape hatch exists |
| F-03 | **HIGH** | Non-string `tenant` → uncaught `AttributeError` → HTTP 500 (and breaks the stated "no oracle" invariant) |
| F-04 | **HIGH** | `protocolVersion` at card top level is not in the 1.0 model; a conformant verifier's signature check **fails** |
| F-05 | **HIGH** | `securitySchemes` uses the 0.x/OpenAPI flat shape, not the 1.0 `SecurityScheme` oneof |
| F-06 | **HIGH** | `securityRequirements` uses the 0.x `[{"scheme": []}]` shape, not `[{"schemes": {…: {"list": […]}}}]` |
| F-07 | **HIGH** | `SendMessage` result is a bare `Task`, not the `SendMessageResponse` oneof `{"task": …}` |
| F-08 | **MEDIUM** | Text `Part` carries the 0.x `"kind"` discriminator, which is not a field in the 1.0 `Part` |
| F-09 | **HIGH** | `POST /v1/a2a` is invisible to `tests/api/test_route_coverage.py` — the gate never saw it |
| F-10 | **MEDIUM** | `canonical_card()` is **not** RFC 8785; six classes of divergence measured |
| F-11 | **MEDIUM** | The card claims `streaming: true` and `extendedAgentCard: true`; both methods answer `-32601` |
| F-12 | **MEDIUM** | Zero integration coverage: no test touches the card, the JWKS, or `/v1/a2a` |
| F-13 | **MEDIUM** | The commit claims an "unsigned card" code path that does not exist |
| F-14 | **MEDIUM** | `ETag` changes on every response and is a hash of a Python `repr()`, not of the served bytes |
| F-15 | **LOW** | `"07"`, `"007"`, `"0001"` are accepted — the exact evasion primitive the docstring names |
| F-16 | **LOW** | JSON-RPC 2.0 conformance: `jsonrpc` never validated; notifications answered; no batch |
| F-17 | **LOW** | Stale `A2A` comment survived the rename at `aegis/src/aegis/agent/graph.py:465` |
| F-18 | **LOW** | `contextId` is set to the tenant id; `-32601` reflects attacker input |

---

## A. Wire-format conformance against A2A 1.0

### What the card actually serves **[MEASURED]**

```
$ curl -s http://127.0.0.1:8110/.well-known/agent-card.json
{"name":"Aegis","description":"A domain-agnostic agentic platform — …","version":"0.1.0",
 "protocolVersion":"1.0",
 "supportedInterfaces":[{"url":"http://127.0.0.1:8110/v1/a2a","protocolBinding":"JSONRPC","protocolVersion":"1.0"}],
 "provider":{"organization":"Aegis","url":"http://127.0.0.1:8110"},
 "documentationUrl":"http://127.0.0.1:8110/docs",
 "capabilities":{"streaming":true,"pushNotifications":false,"extendedAgentCard":true},
 "securitySchemes":{"bearer":{"type":"http","scheme":"bearer","bearerFormat":"JWT","description":"…"}},
 "securityRequirements":[{"bearer":[]}],
 "defaultInputModes":["text/plain"],"defaultOutputModes":["text/plain"],
 "skills":[{"id":"answer-with-provenance",…},{"id":"governed-action",…}],
 "signatures":[{"protected":"eyJhbGciOiJFUzI1NiIs…","signature":"vcmPOBvAiRQ7…"}]}
```

### Required-field audit — **no REQUIRED field is missing**

`a2a.proto:362-399` marks REQUIRED: `name`, `description`, `supported_interfaces`, `version`, `capabilities`, `default_input_modes`, `default_output_modes`, `skills`. All eight are present. `provider` (optional) is present and carries both of its own REQUIRED subfields (`url`, `organization`, `a2a.proto:402-409`). `AgentSkill` REQUIRED = `id`, `name`, `description`, `tags` (`a2a.proto:436-453`) — all present on both skills. `AgentInterface` REQUIRED = `url`, `protocol_binding`, `protocol_version` (`a2a.proto:336-356`) — all present, and `protocolBinding: "JSONRPC"` is one of the three the spec names as officially supported (`a2a.proto:341-343`). `AgentCardSignature` REQUIRED = `protected`, `signature`; `header` optional and correctly omitted (`a2a.proto:457-467`). **This part is right.**

### F-04 — `protocolVersion` at card top level does not exist in A2A 1.0 · **HIGH**

**[SOURCE]** `backend/src/app/a2a/card.py:106`

```python
"protocolVersion": A2A_PROTOCOL_VERSION,
```

**[DOC]** `a2a.proto` — grepping the whole file for `protocol_version` yields **exactly one** hit:

```
355:  string protocol_version = 4 [(google.api.field_behavior) = REQUIRED];   # inside AgentInterface
```

`message AgentCard` (`a2a.proto:362-399`, "Next ID: 20") has no such field. The spec's own sample card (§8.5, `specification.md:2148-2200`) has no top-level `protocolVersion` either. The version lives on the *interface*, which `card.py:113` already does correctly — line 106 is a duplicate in a slot the model does not have.

**Why it is HIGH and not cosmetic.** §8.4.1 rule 1 (`specification.md:2022-2028`) makes the verifier reconstruct the card under protobuf field-presence semantics before canonicalising. A field absent from the model is dropped on that round-trip. Measured, against the live card and the real key:

```
[MEASURED]
SIGNATURE VERIFIES (impl canon): True
verifies after dropping unknown top-level protocolVersion: FAIL <-- spec-conformant verifier breaks
```

So the card *is* correctly signed and *will* fail verification in any client that follows §8.4.1. That is the exact failure mode the commit message says it avoided.

**Fix.** Delete `card.py:106`. Keep `card.py:113`.

### F-05 — `securitySchemes` value is the 0.x flat shape · **HIGH**

**[SOURCE]** `backend/src/app/a2a/card.py:124-135` emits `{"bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT", "description": …}}`.

**[DOC]** `AgentCard.security_schemes` is `map<string, SecurityScheme>` (`a2a.proto:382`), and `SecurityScheme` is a **discriminated union** (`a2a.proto:504-517`):

```protobuf
message SecurityScheme {
  oneof scheme {
    APIKeySecurityScheme api_key_security_scheme = 1;
    HTTPAuthSecurityScheme http_auth_security_scheme = 2;
    ...
  }
}
```

In protobuf JSON the oneof arm is the wrapper key. The spec's own sample (`specification.md:2166-2172`) shows it:

```json
"securitySchemes": { "google": { "openIdConnectSecurityScheme": { "openIdConnectUrl": "…" } } }
```

Two problems in one object: the `httpAuthSecurityScheme` wrapper is missing, and `"type": "http"` is **not a field anywhere in the 1.0 model** — `HTTPAuthSecurityScheme` is `{description, scheme, bearer_format}` only (`a2a.proto:530-540`). `"type"` is the OpenAPI/A2A-0.x discriminator.

**Fix.**
```python
"securitySchemes": {
    "bearer": {
        "httpAuthSecurityScheme": {
            "scheme": "bearer", "bearerFormat": "JWT", "description": "…",
        }
    }
},
```

### F-06 — `securityRequirements` is the 0.x/OpenAPI shape · **HIGH**

**[SOURCE]** `backend/src/app/a2a/card.py:136` → `"securityRequirements": [{"bearer": []}]`

**[DOC]** `repeated SecurityRequirement` (`a2a.proto:384`) where `SecurityRequirement` is `map<string, StringList> schemes = 1` (`a2a.proto:496-499`). Spec sample (`specification.md:2176`):

```json
"securityRequirements": [{ "schemes": { "google": { "list": ["openid", "profile", "email"] } } }]
```

**Fix.** `"securityRequirements": [{"schemes": {"bearer": {"list": []}}}]`

### F-07 — `SendMessage` returns a bare `Task`, not `SendMessageResponse` · **HIGH**

**[MEASURED]**
```
$ POST /v1/a2a {"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{}}
{"jsonrpc":"2.0","id":1,"result":{"id":"29107e58…","status":{"state":"TASK_STATE_SUBMITTED"},"contextId":"1"}}
```

**[DOC]** §9.4.1 (`specification.md:2312-2320`): *"result: SendMessageResponse object, contains one of: `"task"` … `"message"`"*. `a2a.proto:780-788`:

```protobuf
message SendMessageResponse { oneof payload { Task task = 1; Message message = 2; } }
```

An SDK client deserialising `SendMessageResponse` from this payload gets neither arm set — it sees an empty response, and the fields `id`/`status`/`contextId` are unknown to the wrapper.

**Fix.** `rpc_result({"task": as_task(...)}, rpc_id=rpc_id)` at `backend/src/app/a2a/routes.py:135-142`.

### F-08 — the text `Part` carries a 0.x `"kind"` discriminator · **MEDIUM**

**[SOURCE]** `backend/src/app/a2a/rpc.py:118`

```python
result["artifacts"] = [{"artifactId": f"{task}-answer", "parts": [{"kind": "text", "text": text}]}]
```

**[DOC]** `message Part` (`a2a.proto:224-242`) is `oneof content { string text = 1; bytes raw = 2; string url = 3; Value data = 4; }` plus `metadata`, `filename`, `media_type`. There is no `kind`; the oneof arm name *is* the discriminator. Correct JSON is `{"text": "…"}`.

Unreachable today (F-01 means `text` is never non-empty), which is why nothing caught it. Fix now or it ships the day the run is wired.

### F-11 — the card advertises two capabilities the server refuses · **MEDIUM**

**[SOURCE]** `card.py:117-123` sets `"streaming": true` and `"extendedAgentCard": true`. `rpc.py:36` is `A2A_METHODS = ("SendMessage", "GetTask")`.

**[MEASURED]**
```
SendStreamingMessage    {"error":{"code":-32601,"message":"method not found: 'SendStreamingMessage'"}}
GetExtendedAgentCard    {"error":{"code":-32601,"message":"method not found: 'GetExtendedAgentCard'"}}
```

`rpc.py:33-35` states the doctrine this violates verbatim: *"an interface that advertises a method it does not serve is worse than one that advertises fewer."* `card.py:15-21` goes further and justifies withholding the skill catalogue *because* `extendedAgentCard` is where it belongs — a promise to a peer that the server then declines.

**Fix.** Either implement `GetExtendedAgentCard` (the persona-filtered catalogue behind `require_auth` — it is ~15 lines and it is the honest home for the withheld skills) and `SendStreamingMessage` (§9.4.2 is SSE and `/v1/query` already streams), or set both capability booleans to `false`. Do not leave the card claiming them.

### Correct, for the record

- Method names are PascalCase and the 0.x spelling is rejected **[MEASURED]**: `message/send` → `-32601`. §9.4 (`specification.md:2291-2440`) confirms `SendMessage`/`GetTask`.
- `protocolVersion: "1.0"` on the interface, not `"1.0.0"` — §7 forbids the patch component; `a2a.proto:352-355` gives `"0.3"`/`"1.0"` as the examples.
- `supportedInterfaces` rather than the 0.x `url` + `additionalInterfaces` pair — confirmed at `a2a.proto:371` and `specification.md:1998-2007`.
- TaskState strings are the enum's own (`a2a.proto:187-210`).
- `-32700 / -32601 / -32602` match §9.7 (`specification.md:2453-2457`). `-32004` is `UnsupportedOperationError` (`specification.md:1185`) and is within the A2A `-32001..-32099` range — defensible for "no task store", though a client probing a task it just created would expect `-32001 TaskNotFoundError` (`specification.md:1182`). Minor; noted, not scored.
- Well-known paths are at the **root**, not under `/v1`, as §8 requires, and are `include_in_schema=False` — confirmed absent from `/openapi.json` **[MEASURED]**.

### Note, not a finding: the `tenant` field is unreachable from a conformant client

§8.3.2 rule 4 (`specification.md:2013`): a client *"Set[s] the `tenant` field in every request message to exactly the value declared in the selected `AgentInterface` entry (omit the field if `tenant` is not set in that entry)."* Aegis's interface entry declares no `tenant` (`card.py:108-114`), so a conformant peer always omits it and `resolve_addressed_tenant` always takes the `routed is None` path. The refusal is real defence-in-depth against a *non*-conformant caller — worth keeping — but the phase's headline security property is currently exercised only by attackers, never by clients. If multi-agent routing is ever wanted, `AgentInterface.tenant` (`a2a.proto:345-351`) must be populated.

---

## B. The signature

### It is real — verified independently **[MEASURED]**

Reconstructed from the live card and the live JWKS, without touching the signing code:

```
protected header: {'alg':'ES256','jku':'http://127.0.0.1:8110/.well-known/jwks.json',
                   'kid':'6H2MIH2_B7TyBryGlFYHKQ','typ':'JOSE'}
kid matches jwks: True
impl bytes == real JCS bytes for THIS card: True     # rfc8785==0.1.4 as the oracle
SIGNATURE VERIFIES (impl canon): True
TAMPERED verifies: False (good)                       # name -> "Evil"
```

The commit's crypto claim holds. The protected header carries all three MUST parameters (`alg`, `typ`, `kid`) plus the MAY `jku`, per §8.4.2 (`specification.md:2131-2141`); the r‖s → JOSE conversion at `signing.py:127-129` is correct; base64url is unpadded.

### F-10 — `canonical_card()` is not RFC 8785 · **MEDIUM**

**[SOURCE]** `backend/src/app/a2a/signing.py:56`

```python
return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
```

The docstring above it (`signing.py:48`) promises *"RFC 8785 canonical JSON"*. Measured against `rfc8785==0.1.4`:

```
[MEASURED]
DIVERGE | float that is integral (1.0)          impl b'{"a":1.0}'                jcs b'{"a":1}'
DIVERGE | large float 1e16                      impl b'{"a":1e+16}'              jcs b'{"a":10000000000000000}'
DIVERGE | large float 1e20                      impl b'{"a":1e+20}'              jcs b'{"a":100000000000000000000}'
DIVERGE | small float 1e-7                      impl b'{"a":1e-07}'              jcs b'{"a":1e-7}'
DIVERGE | int beyond IEEE754 (2**63)            impl b'{"a":9223372036854775808}' jcs IntegerDomainError
DIVERGE | NaN                                   impl b'{"a":NaN}'                jcs FloatDomainError
DIVERGE | Infinity                              impl b'{"a":Infinity}'           jcs FloatDomainError
DIVERGE | UTF-16 key order U+FFFF vs U+10000    impl {"￿":1,"\U00010000":2} jcs {"\U00010000":2,"￿":1}
DIVERGE | UTF-16 key order U+E000 vs U+1F600    impl {"":1,"\U0001F600":2} jcs {"\U0001F600":2,"":1}
DIVERGE | lone surrogate value                  impl UnicodeEncodeError (→ 500)  jcs CanonicalizationError
MATCH   | non-ASCII string value ("café — 日本")
MATCH   | DEL U+007F in value
MATCH   | control char U+001F
```

Three distinct rule violations:

1. **Numbers.** JCS mandates ECMAScript `Number::toString`. Python's `json` emits `1.0` where JS emits `1`, switches to exponential notation at 1e16 where JS switches at 1e21, and pads the exponent (`1e-07` vs `1e-7`). It also happily emits `NaN`/`Infinity` and arbitrary-precision integers, both of which JCS rejects outright.
2. **Key ordering.** `sort_keys=True` sorts by Unicode **code point**; RFC 8785 §3.2.3 sorts by **UTF-16 code unit**. Any astral-plane key (emoji, most CJK Ext-B) sorts on the wrong side of every key in U+E000–U+FFFF.
3. **Lone surrogates** raise `UnicodeEncodeError` out of `.encode()` rather than a handled canonicalisation error — an uncaught 500 rather than a refusal.

The *current* card contains no numbers and only ASCII keys, so today the bytes coincide — which is exactly why nothing failed and nobody noticed. The blast radius is one field away: an `AgentExtension.params` (`a2a.proto:432`, a `google.protobuf.Struct`), a numeric `metadata` value, or any i18n key turns the card into something no real JCS verifier can reproduce, and the failure is silent on the server side.

**Fix.** `uv add rfc8785` and `return rfc8785.dumps(body)`. It is a small pure-Python package, it is the reference implementation, and it removes a whole class of latent divergence for one line. If a dependency is genuinely unacceptable, at minimum reject non-`str`/`bool`/`int`-in-range values at the door and sort keys by `k.encode("utf-16-be")` — but the dependency is the right answer here, and the plan doc itself already anticipated it (`01-a2a-protocol.md:338`).

### F-02 — the `Host` header rewrites the signed card, including `jku` · **CRITICAL**

**[SOURCE]** `backend/src/app/a2a/routes.py:36-46`

```python
def _origin(request: Request) -> str:
    configured = getattr(get_settings(), "public_base_url", "") or ""
    if configured:
        return str(configured).rstrip("/")
    return str(request.base_url).rstrip("/")
```

**[MEASURED]** `public_base_url` **does not exist on `Settings`** — `grep -rn "public_base_url" backend/src/` returns only this one line, in `routes.py` itself. The `getattr` default therefore always fires, and the origin is *always* the request's Host header. There is no configuration that turns this off.

```
$ curl -s -H 'Host: evil.example.com' http://127.0.0.1:8110/.well-known/agent-card.json
supportedInterfaces: [{'url': 'http://evil.example.com/v1/a2a', 'protocolBinding': 'JSONRPC', 'protocolVersion': '1.0'}]
provider:            {'organization': 'Aegis', 'url': 'http://evil.example.com'}
documentationUrl:    http://evil.example.com/docs
protected header:    {"alg":"ES256","jku":"http://evil.example.com/.well-known/jwks.json",
                      "kid":"6H2MIH2_B7TyBryGlFYHKQ","typ":"JOSE"}
```

The card is **validly signed by Aegis's real key** and tells every peer to send its bearer token to `evil.example.com`, and to fetch the verification key from `evil.example.com` too — so the attacker also controls the JWKS the peer will trust. Served with `Cache-Control: public, max-age=300` (`routes.py:69`), so any shared cache in front of Aegis can be poisoned with the attacker's card for five minutes per poisoning.

This is the precise inversion of the phase's thesis. The signature was introduced so a peer could trust "this card came from the domain that claims it" (`signing.py:6-9`). As shipped, the signature certifies whatever domain the *requester* named.

`X-Forwarded-Host` is correctly ignored **[MEASURED]** — but `X-Forwarded-Proto` **is** honoured (`https://127.0.0.1:8110/…` came back when it was set), so proxy headers are trusted somewhere in the stack, which widens rather than narrows the vector.

**Fix.** Two parts, both needed:
1. Add a real `public_base_url` setting to `Settings` and make it **required** whenever card signing is on. Refuse to sign a Host-derived card. (A card must not be signed at an origin the deployment did not declare.)
2. Add a `TrustedHostMiddleware` with an explicit `allowed_hosts` list.
Until (1) exists, the honest interim is to serve the card unsigned when `public_base_url` is unset — which is what the commit message already claims happens (see F-13).

### F-14 — the `ETag` changes every response and hashes a Python `repr()` · **MEDIUM**

**[SOURCE]** `backend/src/app/a2a/routes.py:68-70`

```python
body = repr(signed).encode()
response.headers["Cache-Control"] = "public, max-age=300"
response.headers["ETag"] = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
```

**[MEASURED]** three consecutive fetches of an unchanged resource:

```
etag: "d0c67cdb19ea5f1fb082814b421881ad"
etag: "249722344035777e03b7cc129b292a22"
etag: "e6348a0592672f90708f0696bc57a62f"
```

Two bugs stacked. ECDSA is randomised, and `sign_card` runs per request, so the entity differs every time and the ETag is never stable — a peer revalidating with `If-None-Match` can never get a 304, which is the entire feature the docstring at `routes.py:60-63` claims to be providing ("lets a peer revalidate cheaply, which the spec asks for"). And `repr()` of a Python dict is not the response body, so even a stable ETag would not be a hash of what was served.

**Fix.** Sign once and memoise per origin (the card is pure given `base_url` — `card.py:88-90` says so), then `ETag = sha256(json.dumps(signed, separators=(",",":")).encode())`. That makes the ETag both stable and correct, and removes a per-request ECDSA operation from an unauthenticated endpoint.

### The ephemeral key, and whether `max-age=300` is safe · **MEDIUM (part of F-14)**

**[MEASURED]** two fresh processes, two different keys:

```
A2A card signing key generated for this process only (kid=dOCk9EgV_gVzDI7_3Psodg). …
A2A card signing key generated for this process only (kid=8SRdIxwOnOJm8lyT6SKsjg). …
```

`signing.py:69-83` is honest about this and logs a warning, which is the right instinct. But `max-age=300` is *not* safe alongside it: for up to five minutes after every restart, caches and peers hold a card signed by a `kid` that is no longer in the JWKS, and the correct verifier behaviour on that is to **reject the card as tampered**. A restart therefore produces a fleet of peers who believe Aegis is serving forged cards. Set `Cache-Control: no-cache` (revalidate every time) while the key is ephemeral, and raise `max-age` only once the key is persisted.

### The JWKS leaks nothing it should not

**[MEASURED]** `{"kty":"EC","crv":"P-256","use":"sig","alg":"ES256","kid":"…","x":"…","y":"…"}` — public coordinates only, no `d`. `kid` is `b64(x[:8] ‖ y[:8])` (`signing.py:73-75`), which is derived *from* public material, so it discloses nothing beyond the key itself. Correct. The card does disclose `PRODUCT_VERSION` (`0.1.0`) and advertises `documentationUrl` → `/docs`, which is a **200 unauthenticated** OpenAPI UI **[MEASURED]** — a pre-existing posture question, but the card now actively points strangers at it. Worth a decision, not scored here.

---

## C. Attacking the tenant refusal

### F-03 — any non-string `tenant` is an uncaught `AttributeError` → HTTP 500 · **HIGH**

**[SOURCE]** `backend/src/app/a2a/rpc.py:78-79`

```python
if not routed.isascii() or not routed.isdecimal():
```

`routed` is typed `str | None`, but the caller hands it raw JSON: `backend/src/app/a2a/routes.py:118` → `routed=params.get("tenant")`. Nothing validates the type. The `except (TypeError, ValueError)` at `rpc.py:88` is *below* this line and cannot catch it.

**[MEASURED]** — unit function:

```
1        -> RAISES AttributeError 'int' object has no attribute 'isascii'
1.0      -> RAISES AttributeError 'float' object has no attribute 'isascii'
True     -> RAISES AttributeError 'bool' object has no attribute 'isascii'
['1']    -> RAISES AttributeError 'list' object has no attribute 'isascii'
{'a': 1} -> RAISES AttributeError 'dict' object has no attribute 'isascii'
```

**[MEASURED]** — live endpoint, `POST /v1/a2a` as `northwind.analyst` (tenant 1):

```
absent                 200 result TASK_STATE_SUBMITTED
null                   200 result TASK_STATE_SUBMITTED
empty                  200 result TASK_STATE_SUBMITTED
correct '1'            200 result TASK_STATE_SUBMITTED
int 1                  500 !! Internal Server Error      <-- 500
int 7                  500 !! Internal Server Error      <-- 500
' 1'                   200 -32602 refused
'1 '                   200 -32602 refused
'1\n'                  200 -32602 refused
'+1'                   200 -32602 refused
'01'                   200 result TASK_STATE_SUBMITTED   <-- accepted, see F-15
'0001'                 200 result TASK_STATE_SUBMITTED   <-- accepted, see F-15
fullwidth '１'          200 -32602 refused
arabic '٧'             200 -32602 refused
superscript '¹'        200 -32602 refused
list ['1']             500 !! Internal Server Error      <-- 500
dict {'id':'1'}        500 !! Internal Server Error      <-- 500
bool true              500 !! Internal Server Error      <-- 500
float 1.0              500 !! Internal Server Error      <-- 500
'1'*4000               200 -32602 refused
'2' (other tenant)     200 -32602 refused
'-1'                   200 -32602 refused
```

Whitespace, `+7`, full-width and Arabic-Indic digits are all correctly refused — the hole the commit describes closing is genuinely closed. But:

**This breaks the invariant the phase is built on.** `rpc.py:24` and the commit message both state: *"Malformed and not-yours fail identically … so the error shape is not an oracle."* It is not true. `{"tenant": 2}` (int) returns **HTTP 500 text/plain**; `{"tenant": "2"}` returns **HTTP 200 JSON-RPC -32602**. A caller distinguishes malformed from not-yours by the *type* of the value it sends, which is precisely the shape-of-failure oracle the design forbids. It is also an unauthenticated-adjacent crash path (auth is required, but any tenant user can trigger it at will) and it produces a traceback in the server log per request.

**Fix.** Type-check first, in `resolve_addressed_tenant`:

```python
if routed is None or routed == "":
    return authenticated
if not isinstance(routed, str):
    raise TenantMismatchError("the addressed tenant is not the tenant this credential belongs to")
```

Note `routed == ""` must stay before the isinstance check only if `False == 0 == ""` semantics are considered — they are not equal, so ordering is safe either way; put the isinstance guard immediately after the `None`/`""` line.

### Related: `params` itself is not type-checked · **HIGH (same root cause)**

**[SOURCE]** `routes.py:100` → `params = body.get("params") or {} if isinstance(body, dict) else {}`. `params` is then `.get()`-ed at lines 118 and 129 without a type check. JSON-RPC 2.0 explicitly permits `params` to be an **Array**.

**[MEASURED]**
```
params as list      500 !! Internal Server Error
params as string    500 !! Internal Server Error
```

**Fix.** `params = body.get("params") if isinstance(body, dict) else None` then `if not isinstance(params, dict): params = {}` — or return `-32602` for a non-object `params`, which is the more correct answer.

### F-15 — leading zeros are accepted · **LOW**

**[MEASURED]** `resolve_addressed_tenant(routed="07", authenticated=7)` → `7`; `"007"` → `7`; live `{"tenant":"0001"}` with tenant 1 → accepted.

`rpc.py:72-77` states the rule this violates in its own words: *"A routing identifier that can be written in forms that differ on the wire but compare equal after parsing is a filter-evasion primitive: whatever logs, rate-limits or blocklists this value sees one string and the comparison sees another."* `"07"` and `"7"` differ on the wire and compare equal after parsing. The ASCII-digit guard closed the Unicode variant of exactly this primitive and left the decimal one open.

**Fix.** Compare canonically: `if routed != str(authenticated): raise …` — one line, and it subsumes the `isascii`/`isdecimal`/`int()` dance entirely.

### F-18 — `contextId` is the tenant id; `-32601` reflects input · **LOW**

`routes.py:139` sets `context=str(auth.tenant_id or "")`, so every task in a tenant shares one `contextId` and the value is the internal tenant primary key. `Task.context_id` (`a2a.proto:171-173`) is meant to identify a *collection of interactions* — a peer using it to continue a conversation will collide every task in the tenant into one thread. For a platform principal it becomes `""`, which is emitted rather than omitted (`rpc.py:113-114` tests `is not None`, not truthiness). Separately, `routes.py:105` echoes attacker input into the error string via `{method!r}`; harmless in JSON but an unnecessary reflection.

---

## D. Route coverage and reachability

### F-09 — `POST /v1/a2a` is invisible to the coverage gate · **HIGH**

`tests/api/test_route_coverage.py:185` iterates **`app.api.routes.router.routes`** and nothing else. The A2A endpoint is declared on a *separate* `APIRouter` (`backend/src/app/a2a/routes.py:33`) which `main.py:804` includes directly on the app.

**[MEASURED]**
```
$ PYTHONPATH=src python -c "import app.main; from app.api.routes import router; \
    print([p for p in (r.path for r in router.routes) if 'a2a' in p]); print(len(router.routes))"
a2a in api router: []
total api router routes: 132
```

So `/v1/a2a` is neither counted as reachable, nor flagged as unreachable, nor listed in `UNREACHABLE_BY_DESIGN`. It **passed by not being seen**. The module docstring says this analysis exists because *"the backend and the browser drifted apart silently and repeatedly"* and reads *"the served route table, read from the live router (not a hand-written list, which would drift the same way)"* — the router it reads is no longer the served route table.

The MCP mount is called out at `main.py:809-813` as deliberately outside the gate; A2A got no equivalent note and no allowlist entry, so this reads as an oversight rather than a decision.

**Fix.** Either add `/v1/a2a` to `UNREACHABLE_BY_DESIGN` with the reason ("protocol endpoint; its clients speak A2A, not the portal"), **and** widen the test to read the app's full route table so the next router-on-the-side cannot repeat this; or, minimally, add the allowlist entry — but the widening is the real fix, because the gate is currently blind to an entire class of route.

### The rest of D is clean

- `/v1/a2a` requires auth: `401 {"detail":"Missing or invalid bearer token."}` with no token and with a bad token **[MEASURED]**.
- Well-known routes are correctly excluded from `/v1` (mounted at root, `main.py:804`) and from the schema (`include_in_schema=False`, `routes.py:49,72`); `/openapi.json` lists `['/v1/a2a']` and neither well-known path **[MEASURED]**.
- `backend/openapi.json` was regenerated in the commit and matches, so `test_openapi_snapshot.py` is satisfied.
- Only `CORSMiddleware` is installed (`main.py:778`), so there is no middleware the new router bypasses.
- Minor: `routes.py:80` hard-codes the literal `"/v1/a2a"` rather than composing `API_PREFIX`. If the version boundary ever moves, this route silently stays behind. One line, worth fixing while nearby.

---

## E. Stale `a2a` vocabulary

The rename `app.a2a.*` → `app.handoff.*` is otherwise complete and well-executed (`aegis/src/aegis/observability/semconv.py:113-126`, applied at `graph.py:469-474` and `subagent.py:539-544`, `scope: "in-process"`). One survivor:

### F-17 — `aegis/src/aegis/agent/graph.py:465` · **LOW**

```python
        # Make the hand-off an explicit, labelled A2A span so the trace shows the
        # supervisor → specialist edge (from/to/reason/protocol) as its own node.
```

The attribute names were renamed; this comment was not. It is the single place left in the product where the word "A2A" labels an in-process dispatch — the exact thing `semconv.py:117-122` says must stop, and exactly what a grepping juror finds. The comment also still says "protocol", which no longer corresponds to any attribute emitted.

**Fix.** `# Make the hand-off an explicit, labelled span so the trace shows the supervisor → specialist edge (from/to/reason/scope) as its own node.`

Everything else that mentions `a2a` is the protocol (`backend/src/app/a2a/**`, `main.py:26,800-804`, `web/src/lib/api/generated/schema.d.ts` — generated, correct) or unrelated third-party prose (`docs/dev_new_docs_v2/research/langflow-and-observability.md:252`, describing Langflow's own route modules — fine).

---

## F-01 — The surface cannot answer anyone · **CRITICAL**

Stated last because it needs the rest as context, but it is the finding that decides the verdict.

Trace the only three doors a peer has **[MEASURED]**:

```
SendMessage           -> {"result":{"id":"…","status":{"state":"TASK_STATE_SUBMITTED"}}}
GetTask (that id)     -> {"error":{"code":-32004,"message":"task history is not retained by this agent;
                                    a task is observable only on the stream that created it"}}
SendStreamingMessage  -> {"error":{"code":-32601,"message":"method not found: 'SendStreamingMessage'"}}
```

**[SOURCE]** `routes.py:135-142` — the comment is candid: *"The run itself is deliberately not wired here yet — this returns a task in SUBMITTED rather than pretending to have completed work it did not do."* And `routes.py:130-134`: *"Task persistence is not built."*

Put together: the task is submitted to nothing, retrievable from nowhere, and the stream the error message points to ("a task is observable only on the stream that created it") **does not exist** — `SendStreamingMessage` is not in `A2A_METHODS`. The `-32004` message therefore directs a peer to a method that returns `-32601`. That is not a candid partial implementation; it is a dead end that reads as a live one.

Meanwhile the signed, public card advertises two concrete skills — `answer-with-provenance` and `governed-action` (`card.py:56-86`) — with example prompts. A juror who runs any A2A client against this card gets a task id and silence, forever, while the card promises answers with provenance. The phase's own framing was that a word without an implementation behind it is *"the one finding here that could have been turned into a credibility problem."* The word now has a JSON document and a signature behind it, but still not an implementation: a peer's question cannot reach the agent and the agent's answer cannot reach the peer.

**Fix (the minimum that makes the phase true).** Wire `SendMessage` to the existing governed run and return the completed `Task` — `/v1/query` already does the work, already scopes by token, already streams. Then either implement `SendStreamingMessage` over the same SSE, or delete the `-32004` message's promise of a stream and set `capabilities.streaming: false`. Until a peer can obtain one answer, the card should not advertise skills.

---

## F-12 — There is no test for any of this · **MEDIUM**

`backend/tests/a2a/test_tenant_refusal.py` is a good test of `resolve_addressed_tenant` as a pure function. It is also the *only* test in the phase.

**[MEASURED]** `grep -rn "v1/a2a\|agent-card\|jwks" backend/tests/` → **no matches**. Nothing exercises the HTTP surface, the card shape, or the signature.

Consequences, each of which is a finding above that a test would have caught:
- Every 500 in F-03 (no test passes a non-`str`).
- F-15 (no `"07"` case in the `garbage` parametrisation at `test_tenant_refusal.py:66`).
- The whole of section A — the card's shape has no assertion of any kind, so F-04…F-08 could not fail.
- The signature. The commit says *"Verified independently against the published JWKS by reconstructing the canonical bytes"* — that was a one-off manual act. There is no test, so the next edit to `card.py` breaks the signature silently, and F-10's latent JCS divergence has nothing watching for it.

The plan asked for exactly these (`01-a2a-protocol.md:439-441`, and V3's *"shuffled — proving JCS, not byte-comparison, is doing the work"*). None were written.

**Fix.** Three tests, all cheap, all against `TestClient`:
1. `GET /.well-known/agent-card.json` → verify the signature against `GET /.well-known/jwks.json`, then re-verify after shuffling the card's key order (proves JCS, not byte-equality), then assert a tampered card fails.
2. Assert the card's shape field-by-field against the 1.0 model — the assertions in section A are the checklist.
3. `POST /v1/a2a` with `tenant` as `int`, `list`, `dict`, `bool`, and with `params` as a list: all must be `-32602`, none may be a 500.

---

## F-13 — The commit describes a code path that does not exist · **MEDIUM**

Commit message `64072d8`:

> *"Without a configured key the card is served unsigned and simply has no signatures array - not a placeholder, not a "signed: false" field a reader would skim past."*

`signing.py:60-83` has **no configuration lookup and no conditional**. `_key()` unconditionally calls `ec.generate_private_key(ec.SECP256R1())` on first use, and `routes.py:66` unconditionally calls `sign_card`. There is no "configured key", no branch that omits `signatures`, and no way to reach the described state. The paragraph in `signing.py:22-27` makes the same claim in the module docstring.

The behaviour described is the *right* behaviour and is the honest interim fix for F-02. But describing it as shipped when it is not is the failure mode the repo's own standard forbids — claiming one thing while running another. Either build the branch (a `Settings.a2a_signing_key`; unset → no `signatures` array) or strike both paragraphs.

---

## F-16 — JSON-RPC 2.0 conformance gaps · **LOW**

**[MEASURED]**

| Sent | Got | Should be |
|---|---|---|
| no `"jsonrpc"` field | `200 result` | `-32600 InvalidRequestError` (`specification.md:2454`) |
| `"jsonrpc": "1.0"` | `200 result` | `-32600` |
| notification (no `id`) | `200 {"id":null,"result":…}` | no response body at all — JSON-RPC 2.0 §4.1 |
| batch `[{…}]` | `-32601 "method not found: None"` | `-32600`, or batch support |
| body is a bare string | `-32601 "method not found: None"` | `-32600` |

None of these is exploitable; all of them are what an interop test suite checks first. `-32700` on malformed JSON with `"id": null` is correct.

---

## F. Regressions — **none** ✅

Both suites run from `backend/` with `.venv/bin/python -m pytest`. **[MEASURED]**

```
$ .venv/bin/python -m pytest tests/ -q
2181 passed, 1 skipped, 4099 warnings in 420.93s (0:07:00)

$ .venv/bin/python -m pytest ../aegis/tests -q
2400 passed, 14 skipped, 1055 warnings in 260.42s (0:04:20)
```

Both counts match the commit message's claim exactly (`backend 2181, aegis 2400`). Nothing is red, and the `app.a2a.*` → `app.handoff.*` span rename broke no assertion.

This is worth stating plainly next to F-12: **the suites being green is not evidence about this phase.** Fourteen of the eighteen findings above are in code that no test in either suite executes.

Pre-existing noise unrelated to Phase 6, noted only so it is not mistaken for new: `tests/ops/test_release.py` has six sync functions carrying `@pytest.mark.asyncio` (lines 74, 80, 84, 89, 94, 225) — pytest warns and skips the mark, so those six tests are running but the marker is dead. `pytest-timeout` is not installed, so `--timeout` is not a usable flag in this venv.

---

## Environment note

`rfc8785==0.1.4` was installed into `backend/.venv` to serve as the independent JCS oracle for F-10, then **uninstalled** — the environment is as found, and `import rfc8785` again raises `ModuleNotFoundError` **[MEASURED]**. No repository source was modified by this audit. If F-10 is fixed as recommended, that package becomes a real dependency and should be added via `uv add rfc8785`.

## What to fix first

1. **F-01** — wire `SendMessage` to the governed run. Nothing else in this list matters if no peer can get an answer.
2. **F-02** — add `Settings.public_base_url` + `TrustedHostMiddleware`; refuse to sign a Host-derived card.
3. **F-03** — one `isinstance(routed, str)` guard, plus a `params` type check. Two lines, removes every 500.
4. **F-04…F-08** — the card and result shapes. Mechanical, ~20 lines, and they are the difference between "speaks A2A" and "emits A2A-shaped JSON".
5. **F-12** — the three tests. Without them findings 4–8 and 10 will regress the first time anyone edits `card.py`.
6. **F-13** — build the unsigned-card branch or strike the claim.
7. **F-09, F-10, F-11, F-14…F-18** — after the above.
