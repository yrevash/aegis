# Phase 0 / M0 — Does Aegis's per-call MCP identity hold on the modern (2026-07-28) path?

**Verdict: PER-CALL IDENTITY HOLDS.** [MEASURED]

Measured on 2026-08-26 against the already-running backend at `http://127.0.0.1:8110`,
MCP endpoint `POST /mcp/mcp`, SDK `mcp==2.0.0`
(`/Users/yrevash/aegis/backend/.venv/lib/python3.11/site-packages/mcp-2.0.0.dist-info`).

Nothing in the repo was modified and the backend was not restarted. Scratch probes live in
`/private/tmp/claude-501/-Users-yrevash-aegis/04394b04-cac4-4f74-91cd-ca75c8311d11/scratchpad/`
(`probe.py`, `probe2.py`, `probe3.py`).

---

## 1. The risk being tested

Aegis's MCP module exists to give **per-call** identity: the principal is re-read from the
bearer token on *every* JSON-RPC message, not once when the connection opens. That property
rests entirely on `ServerRequestContext.request` being populated. If the SDK's modern
(2026-07-28, single-exchange, stateless) request path does not attach the Starlette request to
the per-message context, `resolve_caller` raises — or worse, degrades silently to whatever the
connection was opened with.

The result below is a **positive** one, and it is backed by an executed measurement rather than
by reading the code.

---

## 2. The Aegis code path [SOURCE]

All citations `/Users/yrevash/aegis/backend/src/app/mcp/server.py`.

| What | Where |
| --- | --- |
| `MCP_PATH = "/mcp"` (path inside the mount) | `server.py:163` |
| `MCP_MOUNT = "/mcp"` — full client URL is `<origin>/mcp/mcp` | `server.py:166` |
| `async def resolve_caller(ctx)` | `server.py:489` |
| reads this message's HTTP request | `server.py:521` — `request = getattr(ctx, "request", None)` |
| reads the verified principal off that request's ASGI scope | `server.py:527` — `user = request.scope.get("user")` |
| re-verifies the token **on this call** | `server.py:534` — `claims = decode_access_token(user.access_token.token)` |
| re-reads authority from Postgres **on this call** | `server.py:539` — `return await live_principal(claims.username)` |
| `GovernedMcpServer._caller` → resolves + derives tenant scope | `server.py:1043`, `server.py:1054` |
| `list_tools` calls `_caller` before building the catalog | `server.py:1060` |
| `build_server(..., resolve: CallerResolver = resolve_caller)` | `server.py:1293`, `server.py:1297` |
| `build_http_app` → `server.streamable_http_app(...)` | `server.py:1395`, `server.py:1438` |
| `streamable_http_path=MCP_PATH`, `json_response=False` (default) | `server.py:1439`, `server.py:1401` |
| mounted onto the FastAPI app | `backend/src/app/main.py:783-791` |

`resolve_caller` fails **closed**: no HTTP request on the message → `McpIdentityError`
(`server.py:522-526`); no verified bearer in scope → `McpIdentityError` (`server.py:528-532`).
There is no fallback to a connection-scoped or contextvar identity anywhere in that function.

---

## 3. What the installed SDK actually provides on the modern path [SOURCE]

Paths relative to `/Users/yrevash/aegis/backend/.venv/lib/python3.11/site-packages/`.

1. **Era routing.** `mcp/server/streamable_http_manager.py:183` — if the `MCP-Protocol-Version`
   header is present and is *not* a handshake version, the request goes to
   `handle_modern_request`. So the modern path is reached purely by sending
   `MCP-Protocol-Version: 2026-07-28`; no `initialize`, no `Mcp-Session-Id`.

2. **The request IS attached, per message.** `mcp/server/_streamable_http_modern.py:306`
   (`handle_modern_request`) builds the Starlette `Request` from this POST's scope
   (`request = Request(scope, receive)`, line 322) and puts it on the dispatch context:

   - `_streamable_http_modern.py:399` — the real dispatch:
     `message_metadata=ServerMessageMetadata(request_context=request)`
   - `_streamable_http_modern.py:238` — the same, on the internal `tools/list` walk used to
     resolve a tool's input schema for `Mcp-Param-*` header validation (so even that
     sub-listing is visibility-scoped to *this* caller).

3. **It lands on `ctx.request`.** `mcp/server/runner.py:316` reads
   `request = md.request_context` off the message metadata and `runner.py:336` passes
   `request=request` into `ServerRequestContext`. The field is declared at
   `mcp/server/context.py:47` (`request: RequestT | None = None`), carrier declared at
   `mcp/shared/message.py:39`.

4. **The bearer is verified before the handler runs, per POST.**
   `mcp/server/lowlevel/server.py:773-774` installs Starlette's `AuthenticationMiddleware`
   with `BearerAuthBackend(token_verifier)`, and `lowlevel/server.py:803` wraps the
   streamable-HTTP endpoint in `RequireAuthMiddleware`. Both are ASGI middleware on the route,
   so they run on **every** POST — including every modern single-exchange POST — and it is that
   middleware that puts `AuthenticatedUser` into `request.scope["user"]`, which is exactly what
   `server.py:527` reads.

5. **Note on transport shape.** Aegis passes `json_response=False`, but the modern SSE branch
   collapses to `application/json` when the handler finishes without emitting a notification
   (`_streamable_http_modern.py:442-447`). So the JSON bodies observed below still went through
   the real dispatch site at line 399, not the JSON-only shortcut at line 405.

**Conclusion of the source read:** on the modern path `ctx.request` is populated per message,
from that message's own POST. Aegis's assumption is correct at the source level. The
measurement below confirms it end to end on the running server.

---

## 4. THE MEASUREMENT

### 4.1 Setup — two principals with different roles [MEASURED]

```
$ curl -s -X POST http://127.0.0.1:8110/v1/auth/login -H 'Content-Type: application/json' \
    -d '{"username":"northwind.admin","password":"demo"}'
200  {"role":"admin","token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}

$ curl -s -X POST http://127.0.0.1:8110/v1/auth/login -H 'Content-Type: application/json' \
    -d '{"username":"northwind.analyst","password":"demo"}'
200  {"role":"ai_team","token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}
```

Decoded claims (base64 of the JWT payload):

```
admin             {'username': 'admin',             'role': 'platform_admin', 'coarse_role': 'admin',    'tenant_id': None, 'sub': '1'}
northwind.admin   {'username': 'northwind.admin',   'role': 'tenant_admin',   'coarse_role': 'admin',    'tenant_id': 1,    'sub': '6'}
northwind.analyst {'username': 'northwind.analyst', 'role': 'ai_team',        'coarse_role': 'ai_team',  'tenant_id': 1,    'sub': '7'}
```

### 4.2 The modern path is reachable and answers 200 [MEASURED]

The modern envelope requires a `params._meta` carrying the protocol version and client
capabilities, plus routing headers that must agree with the body (`mcp-method`, and `mcp-name`
for `tools/call`). Two rejections were observed while working that out, and are reported here
because they are part of the raw record:

```
# no _meta:
{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"params._meta must be an object carrying the required 'io.modelcontextprotocol/protocolVersion' and 'io.modelcontextprotocol/clientCapabilities' envelope keys"}}

# no mcp-method header:
{"jsonrpc":"2.0","id":1,"error":{"code":-32020,"message":"mcp-method header does not match the request body's method"}}
```

The working call:

```
$ curl -s -i -X POST http://127.0.0.1:8110/mcp/mcp \
   -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
   -H 'Accept: application/json, text/event-stream' \
   -H 'MCP-Protocol-Version: 2026-07-28' -H 'mcp-method: tools/list' \
   -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'

HTTP/1.1 200 OK
date: Wed, 26 Aug 2026 20:20:22 GMT
server: uvicorn
content-length: 6752
content-type: application/json

{"jsonrpc":"2.0","id":1,"result":{"cacheScope":"private","resultType":"complete","tools":[{"annotations":{...,"title":"add_case_note (low risk)"},...
```

Note: **no `mcp-session-id` response header** — this is the stateless single-exchange path, not
the 2025 session path.

### 4.3 The per-persona tool allowlist differs by principal [MEASURED]

Same request, only the bearer changed:

```
== northwind.admin
['add_case_note', 'aegis_audit_recent', 'assign_request', 'find_requests', 'update_request_status']
== northwind.analyst
['add_case_note', 'assign_request', 'find_requests', 'update_request_status']
```

`aegis_audit_recent` is admin-only and is present for one principal and absent for the other.

### 4.4 THE ACTUAL TEST — two different bearers over ONE TCP connection [MEASURED]

Driven by a raw-socket client (`scratchpad/probe.py`) so that "same connection" is not a
matter of trusting an HTTP library's pooling: one `socket.create_connection`, HTTP/1.1
keep-alive, four sequential requests, socket never closed in between. The local port is printed
before and after every request.

```
$ /Users/yrevash/aegis/backend/.venv/bin/python probe.py aegis_audit_recent '{"limit":2}'

LOCAL SOCKET (same connection for both calls): ('127.0.0.1', 58047)

CALL 1  bearer=northwind.admin  socket=('127.0.0.1', 58047)
  HTTP: HTTP/1.1 200 OK
  tools: ['add_case_note', 'aegis_audit_recent', 'assign_request', 'find_requests', 'update_request_status']
  error: None

CALL 2  bearer=northwind.analyst  socket=('127.0.0.1', 58047)
  HTTP: HTTP/1.1 200 OK
  tools: ['add_case_note', 'assign_request', 'find_requests', 'update_request_status']
  error: None

TOOLS/CALL as northwind.admin socket=('127.0.0.1', 58047) -> HTTP/1.1 200 OK
{"jsonrpc":"2.0","id":9,"result":{"content":[{"text":"{\"tenant_id\": 1, \"scope\": \"tenant:1\", \"count\": 2, \"rows\": [{\"id\": 13083, \"ts\": \"2026-08-26T20:21:05.257924+00:00\", \"action\": \"auth.login\", \"actor\": \"northwind.admin\", ...}]}","type":"text"}],"isError":false,"resultType":"complete","structuredContent":{"tenant_id":1,"scope":"tenant:1","count":2,...},"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"tcs-adapter-tools","version":"2.0.0"}}}}

TOOLS/CALL as northwind.analyst socket=('127.0.0.1', 58047) -> HTTP/1.1 200 OK
{"jsonrpc":"2.0","id":9,"result":{"content":[{"text":"Tool 'aegis_audit_recent' requires one of the roles: admin, devops. This account holds 'ai_team'.","type":"text"}],"isError":true,"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"tcs-adapter-tools","version":"2.0.0"}}}}

FINAL SOCKET: ('127.0.0.1', 58047)
```

The backend's own access log agrees that all four POSTs arrived on the one client port
(`scratchpad/api.log`):

```
21975:INFO:     127.0.0.1:58047 - "POST /mcp/mcp HTTP/1.1" 200 OK
22008:INFO:     127.0.0.1:58047 - "POST /mcp/mcp HTTP/1.1" 200 OK
22075:INFO:     127.0.0.1:58047 - "POST /mcp/mcp HTTP/1.1" 200 OK
22143:INFO:     127.0.0.1:58047 - "POST /mcp/mcp HTTP/1.1" 200 OK
```

**This is the result.** On one connection, without any re-handshake, the second bearer produced
a different tool catalogue and a role refusal naming the *second* principal's live role
(`This account holds 'ai_team'`). Identity did not stick to the connection.

### 4.5 Cross-tenant switch on one connection [MEASURED]

`probe2.py`, same single socket, three `tools/call aegis_audit_recent` in a row with three
different bearers:

```
LOCAL SOCKET: ('127.0.0.1', 58268)

TOOLS/CALL as admin              socket=('127.0.0.1', 58268) -> HTTP/1.1 200 OK
  "structuredContent":{"tenant_id":null,"scope":"all-tenants","count":1,
     "rows":[{"id":13090,"ts":"2026-08-26T20:21:41.501858+00:00","action":"auth.login","actor":"admin",...}]}

TOOLS/CALL as northwind.admin    socket=('127.0.0.1', 58268) -> HTTP/1.1 200 OK
  "structuredContent":{"tenant_id":1,"scope":"tenant:1","count":1,
     "rows":[{"id":13089,"ts":"2026-08-26T20:21:24.986124+00:00","action":"ml.explain","actor":"northwind.admin",...}]}

TOOLS/CALL as northwind.analyst  socket=('127.0.0.1', 58268) -> HTTP/1.1 200 OK
  "content":[{"text":"Tool 'aegis_audit_recent' requires one of the roles: admin, devops. This account holds 'ai_team'.","type":"text"}], "isError":true

FINAL SOCKET: ('127.0.0.1', 58268)
```

```
$ grep -c "58268" scratchpad/api.log
5
22339:INFO:     127.0.0.1:58268 - "POST /mcp/mcp HTTP/1.1" 200 OK
22372:INFO:     127.0.0.1:58268 - "POST /mcp/mcp HTTP/1.1" 200 OK
22439:INFO:     127.0.0.1:58268 - "POST /mcp/mcp HTTP/1.1" 200 OK
22506:INFO:     127.0.0.1:58268 - "POST /mcp/mcp HTTP/1.1" 200 OK
22574:INFO:     127.0.0.1:58268 - "POST /mcp/mcp HTTP/1.1" 200 OK
```

The **tenant authority** changed mid-connection too: `scope: "all-tenants"` for the untenanted
platform admin, `scope: "tenant:1"` for the tenant admin. That is `auth.tenant_scope()`
(`server.py:1054`) being recomputed per call, not per connection.

### 4.6 Negative control — the token is re-verified per call [MEASURED]

`probe3.py`: valid bearer, then a tampered bearer (last 4 signature chars replaced), then the
valid bearer again — all on **one** socket.

```
SOCKET: ('127.0.0.1', 58274)
call 1 (valid bearer)  -> HTTP/1.1 200 OK complete
call 2 (tampered bearer, SAME socket) -> HTTP/1.1 401 Unauthorized
   www-authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource"
   body: {"error": "invalid_token", "error_description": "Authentication required"}
call 3 (valid again, SAME socket) -> HTTP/1.1 200 OK complete
SOCKET: ('127.0.0.1', 58274)
```

A connection-scoped design would have let call 2 through on the strength of call 1. It did not.
Equally, the 401 did not poison the connection: call 3 succeeded.

### 4.7 Negotiated protocol revision, and `server/discover` [MEASURED]

`server/discover` on the modern path:

```
HTTP/1.1 200 OK
content-type: application/json

{"jsonrpc":"2.0","id":5,"result":{"cacheScope":"private",
 "capabilities":{"resources":{"listChanged":false,"subscribe":false},"tools":{"listChanged":false}},
 "instructions":"Adapter action tools for the service-request domain, plus Aegis platform reads. Every call is authorised against the caller's live role and tenant, so the tools you see are the tools you may call. HIGH-risk writes are filed at the human approval gate and are never executed over MCP. ...",
 "resultType":"complete","supportedVersions":["2026-07-28"],"ttlMs":0,
 "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"tcs-adapter-tools","version":"2.0.0"}}}}
```

`server/discover` **responds**, and advertises `supportedVersions: ["2026-07-28"]`.

The legacy handshake still works alongside it (so 2025-era clients are not broken):

```
$ curl ... -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18",...}}'
HTTP/1.1 200 OK
content-type: text/event-stream
mcp-session-id: 08b401dd67894e9c8ffb94524de7c25c

event: message
data: {"jsonrpc":"2.0","id":1,"result":{...,"protocolVersion":"2025-06-18","serverInfo":{"name":"tcs-adapter-tools","version":"2.0.0"}}}
```

The server's **own OpenTelemetry span** for the calls in §4.5 records the revision it served
(`scratchpad/api.log`, around line 22540):

```
    "name": "tools/call aegis_audit_recent",
    "kind": "SpanKind.SERVER",
    "start_time": "2026-08-26T20:21:52.005231Z",
    "status": { "status_code": "ERROR" },
    "attributes": {
        "mcp.method.name": "tools/call",
        "mcp.protocol.version": "2026-07-28",
        "jsonrpc.request.id": "9",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "aegis_audit_recent",
        "error.type": "tool_error"
    },
```

That `mcp.protocol.version: 2026-07-28` is the server saying, from the inside, that the modern
revision is what it served — and this particular span is the analyst's refusal, i.e. the
governance decision was made on the modern path.

---

## 5. Verdict

**PER-CALL IDENTITY HOLDS on the modern 2026-07-28 stateless path.** [MEASURED]

The single most important measurement: on **one TCP connection (port 58047 / 58268)**, swapping
the bearer changed the advertised tool catalogue (`aegis_audit_recent` present for
`northwind.admin`, absent for `northwind.analyst`), changed the tenant authority
(`all-tenants` → `tenant:1`), and produced a role refusal naming the *current* call's live role
— with no re-handshake and no new session.

Supporting facts:

- `ctx.request` is populated per message on the modern path — `_streamable_http_modern.py:399`
  → `runner.py:316` → `runner.py:336` → `context.py:47`. [SOURCE]
- The bearer is verified per POST by route middleware, which is what fills
  `request.scope["user"]` that `server.py:527` reads. [SOURCE]
- A tampered bearer mid-connection is rejected 401 and a subsequent valid bearer still
  succeeds on that same socket. [MEASURED]
- `server/discover` responds and advertises `supportedVersions: ["2026-07-28"]`; the 2025-06-18
  handshake still negotiates successfully. [MEASURED]

### Nothing to fix

No degradation was found, so there is no fix to specify. Two things worth carrying forward as
*watch items* rather than defects:

1. **The property is guarded only by `ctx.request` staying populated.** The SDK marks that
   plumbing as provisional — `runner.py:305-307` carries a `TODO(L54): remove for Context
   rework. Reads the SHTTP per-request data off the raw dctx.message_metadata carrier; replace
   with the per-transport context once that lands.` [SOURCE] An `mcp` SDK upgrade past 2.0.0 is
   the specific event that could silently break this. The measurement in §4.4 is the
   regression test to re-run after any such upgrade; it is cheap and unambiguous.

2. **Aegis fails closed if it does break.** `server.py:522-526` raises `McpIdentityError` when
   `ctx.request` is `None`, and `resolve_caller` has no contextvar fallback — so the dangerous
   silent-degradation mode is structurally excluded: a broken `ctx.request` becomes a hard error
   on every call, not a stale identity. [SOURCE]

Read-only MCP tool calls did not produce `audit` rows in this run (the newest rows were
`auth.login` and `ml.explain`), so per-call identity was evidenced from the tool results and the
OTel spans rather than from the audit ledger.
