# SOTA-05 — SBOM in CI, and an Agent Bill of Materials

> **Status: PLAN. Nothing here is implemented.** Written 2026-08-27.
>
> **Evidence marks, as `phase-11-langflow.md` uses them.** `[SOURCE path:line]` — read in this
> repository at that line, on this branch, today. `[MEASURED]` — a command was run on this
> checkout and this is its output. `[DOC url]` — vendor or standards-body documentation,
> fetched. Where this document asserts something none of those establish, **it says so in the
> same sentence.**

---

## Correcting the brief before anything else

**The brief said Aegis "has NO SBOM anywhere". That is wrong, and the plan changes if you believe
it.** Aegis ships a working SBOM export today:

* `GET /v1/stack/sbom?format=cyclonedx|spdx` `[SOURCE backend/src/app/api/routes.py:1637-1665]`
* CycloneDX **1.6** and SPDX **2.3**, both generated from **one** inventory pass so they cannot
  describe different machines `[SOURCE backend/src/app/platform/sbom.py:192, :266, :25-27]`
* Every component carries a PURL — the key an advisory database joins on `[SOURCE :133-162]`
* Served with the format's own media type so a scanner consumes it directly `[SOURCE
  backend/src/app/api/routes.py:1658-1664]`
* Downloadable from the DevOps console `[SOURCE web/src/components/devops/PatchCheck.tsx:512]`
* And it already states its own limits rather than implying them: *"neither document is signed,
  and there is no in-toto/SLSA provenance attestation"* `[SOURCE
  backend/src/app/platform/sbom.py:29-33]`

So this plan is **not** "add an SBOM". It is three narrower things, each of which is a real gap:

1. **The SBOM describes the running interpreter, not the branch.** `resolve_inventory` walks
   `importlib.metadata.distributions()` `[SOURCE backend/src/app/platform/sbom.py:133-162]`. That
   is the right source for "what is this box running", and the wrong source for a supply-chain
   gate, which has to answer *before* deploy and has to be reviewable in a pull request.
2. **CI generates no SBOM at all.** The workflow runs ruff, three suites, the OpenAPI snapshot
   check and an OSV advisory gate `[SOURCE .github/workflows/ci.yml:40-181]` — no SBOM step, no
   artifact, nothing diffable.
3. **There is no AgBOM.** No endpoint, no module, nothing that inventories the *agent* — its
   tools, their risk tiers, the model fleet, the rails, the knowledge sources. `grep -ri agbom`
   over the repository returns nothing `[MEASURED]`.

---

## The supply-chain event this is a response to, verified

In March 2026 an actor published backdoored **litellm 1.82.7** and **1.82.8** directly to PyPI,
bypassing the project's normal GitHub release process. The releases carried a malicious
`litellm_init.pth` that executes on **every Python process start** in any environment where
litellm is installed, delivering a credential harvester, a Kubernetes lateral-movement toolkit and
a persistent backdoor. 1.82.8 went live at 10:52 UTC on 24 March 2026 and the packages were live
roughly 40 minutes before PyPI quarantined them
`[DOC https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/]`
`[DOC https://www.bitsight.com/blog/litellm-versions-1-82-7-1-82-8-supply-chain-compromise]`.
Datadog attributes it to **TeamPCP**, in a campaign that also hit Trivy, KICS and the Telnyx
Python SDK `[DOC ibid.]`. The FBI published **FLASH-20260702-01** on 2 July 2026
`[DOC https://www.ic3.gov/CSA/2026/260702.pdf]`. CloudSEK's reconstructed dataset covers **more
than 2,500 organisations and approximately 434,000 CI/CD pipelines** potentially exposed —
and is careful to say the figures "do not establish that every organisation or pipeline executed
the malicious package"
`[DOC https://www.cloudsek.com/blog/ai-supply-chain-breach-2500-companies-434000-cicd-pipelines]`.
**Quote that caveat whenever the number is quoted**; a headline count presented as a breach count
is the kind of claim this repository does not make.

### Where Aegis actually stands — and the finding

Aegis's threat model defends **ASI04 Agentic Supply Chain** with: *"Single vetted model gateway;
pinned deps; no arbitrary local model loading"* `[SOURCE docs/security/threat-model.md:38]`.
Unpack "pinned deps" against the files:

| Fact | Evidence |
|---|---|
| The **declared** dependency is a floor, not a pin | `"litellm>=1.52"` `[SOURCE backend/pyproject.toml:13]` — a range that **includes 1.82.7 and 1.82.8** |
| The aegis package declares the same floor | `gateway = ["litellm>=1.52"]` `[SOURCE aegis/pyproject.toml:73]` |
| The actual pin lives in a **uv resolution constraint**, and its stated reason is a functional regression, not security | *"litellm 1.96.2 regressed the gateway path; 1.96.0 is the verified one"* `[SOURCE backend/pyproject.toml:217]`, `"litellm==1.96.0"` `[SOURCE backend/pyproject.toml:223]` |
| The lockfile records that constraint | `{ name = "litellm", specifier = "==1.96.0" }` `[SOURCE backend/uv.lock:25]` |
| The installed version on this machine is clean | `backend/.venv/.../litellm-1.96.0.dist-info` `[MEASURED]` |
| The lockfiles are genuinely hash-pinned | `grep -c "sha256:"` → **4,219** in `backend/uv.lock`, **2,148** in `aegis/uv.lock` `[MEASURED]` |

**Aegis is clean.** 1.96.0 is well above the backdoored releases. But *why* it is clean is worth
saying precisely, because the sentence in the threat model is doing more work than the files
support:

> **CI does not install from the lockfile.** The workflow runs `uv venv` then
> `uv pip install -e ".[data,auth,observability,agent,retrieval,ingestion,ml,guardrails,mcp,dev]"`
> `[SOURCE .github/workflows/ci.yml:112-114]` — `uv pip install`, not `uv sync`. uv's settings
> reference documents `constraint-dependencies` for **`uv lock`, `uv sync` and `uv run`** and does
> not list `uv pip install` `[DOC https://docs.astral.sh/uv/reference/settings/]`. If that reading
> is right, a CI resolve is bounded only by `litellm>=1.52`, and during a 40-minute window on
> 24 March 2026 the newest release satisfying that floor was a backdoored one.

**This reading is not verified.** It is an inference from the settings documentation plus the CI
file, and the exact check is one command, which task S0 makes the first thing anyone does:

```bash
cd backend && uv venv /tmp/probe && VIRTUAL_ENV=/tmp/probe uv pip install --dry-run -e ".[data]" 2>&1 | grep -i litellm
```

If it resolves to something other than 1.96.0, "pinned deps" is not describing CI, and the fix
(switch CI to `uv sync --frozen`) is a two-line change worth far more than the whole rest of this
document. **Do this before writing any other code in this plan.**

---

## What an AgBOM is, and what shape to build

**AgBOM = Agent Bill of Materials**: a dynamic inventory of every component in an agent system —
tools, models, capabilities, knowledge sources, memory and dependencies. It is deliberately not a
new format: the OWASP Agent Observability Standard's **Inspect** layer *extends* CycloneDX, SPDX
and SWID rather than inventing a fourth `[DOC https://aos.owasp.org/spec/inspect/]`. Its entity
table is:

| AgBOM entity | Parameters `[DOC https://aos.owasp.org/spec/inspect/]` |
|---|---|
| Standard Packages | Name, Description, Version |
| Models | Name, Version, Description, Endpoint, Context Window, Args |
| Capabilities | Agent Card definitions, discovered Agents list, MCP servers and parameters |
| Knowledge | Name, Description, Schema, Search type, Search args |
| Memory | Name, Description, Type, Size, Search args, Window size, Path |
| Tools | Name, Description, Scheme, Endpoint (local/MCP) |

The CycloneDX binding uses `specVersion: "1.6"`, `type: "service"` for the agent itself and
`type: "tool"` for capabilities, with everything agent-specific carried as `properties` key/value
pairs (`model`, `modelContextWindow`, `memoryBackend`, `memoryLimitMB`, `a2aCardUrl`, `compliance`,
…) and a `dependencies` section mapping the agent to the tools it depends on
`[DOC https://aos.owasp.org/spec/inspect/extend_cyclonedx/]`. **That page marks itself "currently
under development"** `[DOC ibid.]`, and the SPDX and SWID bindings are marked "help wanted"
`[DOC https://aos.owasp.org/spec/inspect/]`.

**Two cautions, both of which shape the build:**

1. **`"type": "tool"` is not a CycloneDX 1.6 component type.** CycloneDX carries a `metadata.tools`
   field, which is a different thing entirely; the `component.type` enum is
   `application | framework | library | container | platform | operating-system | device |
   device-driver | firmware | file | machine-learning-model | data | cryptographic-asset`. If that
   is right, the AOS example document **fails CycloneDX 1.6 schema validation**. I did not
   validate the AOS example against the official schema, so this is an inference — task S4's first
   test settles it by running our own output through the published JSON Schema. **Emit
   `type: "application"` for tools until a validator says otherwise, and record the divergence
   from the AOS example in the module docstring.** A document that does not validate is a document
   a buyer's scanner rejects, which is the entire reason we are emitting a standard format.
2. **The relationship between AOS and the Agent Control Standard.** ACS launched 27 May 2026 at
   Microsoft Build `[DOC https://www.businesswire.com/news/home/20260527326259/en/]` with the same
   Instrument / Trace / Inspect three-layer shape and the same "extends CycloneDX, SPDX and SWID"
   AgBOM story. **The specification pages I could fetch and read are the OWASP AOS ones**; the ACS
   repository's README points at a `/specification` folder and a docs site whose spec pages I did
   not retrieve `[MEASURED — WebFetch of github.com/Agent-Control-Standard/ACS returned the README
   only]`. Cite AOS in anything jury-facing until an ACS spec page has been read directly.

---

## Aegis already holds every input — verified, one by one

| AgBOM entity | Where it lives in this repo | Verified |
|---|---|---|
| **Models** | `_FLEET_DECLARATION` — 12 deployments, each with `id`, `role`, `input_rate`, `output_rate`, `tenant_selectable` `[SOURCE aegis/src/aegis/gateway/routing.py:138-176]`; the role→deployment routing table `[SOURCE :59-66]`; `allowed_deployments()` `[SOURCE :207]`; `tenant_selectable_deployments()` `[SOURCE :212]`; the roles reserved for the host's own safety layers `[SOURCE :117-124]` | ✅ |
| **Tools** | `TOOL_REGISTRY` — 4 tools `[SOURCE backend/src/app/adapter/tools.py:694-758]`; each a `ToolSpec` with `risk: RiskLevel`, `read_only`, `destructive`, `idempotent` and a JSON-Schema `args_model` `[SOURCE :625-670]`; `RiskLevel` is `LOW`/`MEDIUM`/`HIGH` `[SOURCE aegis/src/aegis/core/types.py:25-36]`; per-persona `ALLOWLIST` `[SOURCE backend/src/app/adapter/tools.py:760-778]` | ✅ |
| **Capabilities** | `AEGIS_MODULES` — 15 modules, each with branded name, honest `tech`, `module_path` and `live`/`optional` status `[SOURCE backend/src/app/capabilities.py:73-199]`, already served at `GET /v1/platform/capabilities` `[SOURCE backend/src/app/api/routes.py:1417-1444]` | ✅ |
| **Guardrails** | The per-tenant `GuardrailPolicy` field list `[SOURCE aegis/src/aegis/guardrails/policy.py:33-84]`; the rail stages `[SOURCE aegis/src/aegis/core/types.py:69-83]`; the named layers (`injection`, `injection_unavailable`, …) `[SOURCE aegis/src/aegis/guardrails/pipeline.py:55-59]`; live posture from `aegis.security.security_posture` `[SOURCE backend/src/app/api/routes.py:4044-4048]` | ✅ |
| **Knowledge** | The retrieval modules in the capability manifest (`app.retrieval.pipeline`, `app.retrieval.cache`) `[SOURCE backend/src/app/capabilities.py:104-116]`; the write-time gate `[SOURCE aegis/src/aegis/retrieval/validation.py:53]` | ✅ (module-level; per-corpus detail is not modelled anywhere — see "does not cover") |
| **Memory** | `MemoryConfig`'s knobs including `memory_backend` `[SOURCE aegis/src/aegis/memory/config.py:48-90]`; the three tiers `[SOURCE aegis/src/aegis/memory/stores.py:65-197]` | ✅ |
| **Dependencies** | `resolve_inventory()` `[SOURCE backend/src/app/platform/sbom.py:133]` and `lockfile_digest_count()` `[SOURCE :168]` over `("backend/uv.lock", "aegis/uv.lock")` `[SOURCE :64]` | ✅ |
| **MCP peers** | A comma-separated `id=url` list of external MCP servers in settings `[SOURCE backend/src/app/config.py:484]` | ✅ |

**Every input exists. Nothing has to be invented, and nothing may be.** The AgBOM is a projection,
in exactly the sense `GET /platform/capabilities` already is — which is why it must be *derived*
from these structures at request time and never hand-maintained. A hand-written AgBOM drifts from
the platform it describes on the first tool added, and a bill of materials that is wrong is worse
than none, because a scanner believes it — the argument `sbom.py:12-15` already makes for the
package list.

**One drift to fix while in here:** `sbom.py`'s docstring claims *"`uv.lock`'s 4,219 sha256
digests"* `[SOURCE backend/src/app/platform/sbom.py:31-32]`, but `_LOCKFILES` counts **both**
lockfiles `[SOURCE :64]`, so the function actually returns **6,367** `[MEASURED: 4219 + 2148]`.
The number in the prose is stale.

---

## Files to create and modify

### A. The CI SBOM

**`.github/workflows/ci.yml`** — a new `sbom` job, after `python`.

**The generator choice, measured.** `uv export --format cyclonedx1.5` emits a CycloneDX 1.5 JSON
document straight from `uv.lock` `[DOC https://docs.astral.sh/uv/concepts/projects/export/]`. But
**the uv on this machine cannot do it**: `uv --version` → `uv 0.6.7 (029b9e1fc 2025-03-17)` and
`uv export --format` reports `[possible values: requirements-txt]` `[MEASURED]`. CI installs uv via
`astral-sh/setup-uv@v5` with no version pin `[SOURCE .github/workflows/ci.yml:45]`, so **CI has a
newer uv than the developer machine** — a drift that is fine until the day it is not.

Decision: **pin the uv version in the workflow** (`with: version: "<the one verified to export
cyclonedx1.5>"`), for the same reason ruff is pinned to `0.16.2` there and for the reason the
comment gives — *"a gate that floats decides one day that code nobody touched is now wrong"*
`[SOURCE .github/workflows/ci.yml:53-59]`. Then:

```yaml
  sbom:
    name: SBOM (CycloneDX from the lockfiles)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with: { version: "<pinned>" }
      - name: Backend SBOM
        run: uv export --project backend --format cyclonedx1.5 --all-extras > sbom-backend.cdx.json
      - name: Aegis SBOM
        run: uv export --project aegis  --format cyclonedx1.5 --all-extras > sbom-aegis.cdx.json
      - name: The lockfiles are in step with the manifests
        run: uv lock --project backend --check && uv lock --project aegis --check
      - uses: actions/upload-artifact@v4
        with: { name: sbom, path: "*.cdx.json" }
```

`uv lock --check` is the step that makes the artifact mean something: an SBOM exported from a
lockfile that no longer matches its `pyproject.toml` describes a resolve nobody is running.

**Note the version mismatch and do not paper over it.** `uv export` emits CycloneDX **1.5**
`[DOC https://docs.astral.sh/uv/concepts/projects/export/]`; `sbom.py` emits **1.6** `[SOURCE
backend/src/app/platform/sbom.py:58]`. Two documents at two spec versions describing overlapping
sets is confusing, and the honest fix is a sentence in the artifact's README saying which question
each answers — the CI one answers *"what does this branch resolve to"*, the endpoint one answers
*"what is this box running"* — rather than pretending they are the same artifact.

**Fallback if the pinned uv still cannot export CycloneDX:** `cyclonedx-py` has no first-class
uv-project support (tracked as CycloneDX/cyclonedx-python issues #907 and #1029)
`[DOC https://github.com/CycloneDX/cyclonedx-python/issues/1029]`, but it does support scanning a
virtual environment, and `uv sync --frozen` followed by `cyclonedx-py environment` produces a 1.6
document from the locked resolve. Slower, one more dependency, same answer. Choose it only if the
`uv export` route fails.

### B. The AgBOM

**New — `backend/src/app/platform/agbom.py`.** Sits beside `sbom.py`, imports from it (`Component`,
`resolve_inventory`, `lockfile_digest_count`, `CYCLONEDX_SPEC_VERSION`) so the two documents share
one package inventory and cannot describe different machines — the property `sbom.py:25-27` already
holds for its own pair.

```python
def build_agbom() -> dict[str, object]:
    """Return a CycloneDX 1.6 document describing the AGENT, not just its packages."""
```

Structure:

* `metadata.component` — `type: "application"`, `name: "Aegis"`, `version: PRODUCT_VERSION`
  `[SOURCE backend/src/app/capabilities.py:31]`, `bom-ref: "aegis"`.
* **Models** — one component per `_FLEET_DECLARATION` entry `[SOURCE
  aegis/src/aegis/gateway/routing.py:138-176]`, `type: "machine-learning-model"`, properties:
  `aegis:model:role`, `aegis:model:tenant-selectable`, `aegis:model:input-rate-per-1k`,
  `aegis:model:output-rate-per-1k`, `aegis:model:is-platform-default` (from `_DEFAULT_ROUTING`
  `[SOURCE :59-66]`), and `aegis:model:guardrail-reserved` for the `CHEAP`/`VISION` roles
  `[SOURCE :117-124]`. **Twelve components, and the count is an assertion a test holds.**
* **Tools** — one component per `TOOL_REGISTRY` entry `[SOURCE
  backend/src/app/adapter/tools.py:694]`, `type: "application"` (see the caution above), with
  `aegis:tool:risk` (`low`/`medium`/`high`), `aegis:tool:read-only`, `aegis:tool:destructive`,
  `aegis:tool:idempotent` `[SOURCE :625-670]`, and `aegis:tool:personas` from `ALLOWLIST`
  `[SOURCE :760-778]`. **The risk tier is the field that makes this an AgBOM rather than a list of
  function names** — it is what tells a reader which capabilities route through the human gate.
* **Guardrails** — one component per named rail layer, `type: "application"`, with
  `aegis:rail:stage` (now four stages if SOTA-03 lands) and `aegis:rail:blocking`. Pull the live
  posture from `aegis.security.security_posture(read_signals())` `[SOURCE
  backend/src/app/api/routes.py:4044-4048]` so a rail that is *configured* and a rail that is
  *running* are distinguishable — a rail listed as present but not wired is exactly the lie an
  AgBOM exists to prevent.
* **Knowledge and memory** — one component each for the retrieval pipeline and the memory tiers,
  with `aegis:memory:backend` from `MemoryConfig` `[SOURCE aegis/src/aegis/memory/config.py]` and
  the retrieval modules from the capability manifest.
* **Capabilities** — the 15 `AEGIS_MODULES` `[SOURCE backend/src/app/capabilities.py:73]` as
  components with `aegis:module:tech`, `aegis:module:path`, `aegis:module:status`.
* **Packages** — `resolve_inventory()`, unchanged, with PURLs.
* **`dependencies`** — `aegis` `dependsOn` every tool, model and rail ref. This is the section that
  makes the document a graph rather than a list, and it is what the AOS CycloneDX binding uses
  `[DOC https://aos.owasp.org/spec/inspect/extend_cyclonedx/]`.
* **Integrity evidence** — `lockfile_digest_count()` as `aegis:supply-chain:lockfile-sha256-pins`,
  and an explicit `aegis:supply-chain:signed: "false"` property. **Say the unsigned part in the
  document**, exactly as `sbom.py:29-33` already does for its own.

**New route — `backend/src/app/api/routes.py`**, beside `/platform/capabilities` (`:1417`):

```python
@router.get("/platform/agbom", tags=["platform"])
async def platform_agbom(
    auth: AuthContext = Depends(require_admin_or_devops),
) -> JSONResponse: ...
```

`require_admin_or_devops`, matching `GET /stack/sbom` `[SOURCE backend/src/app/api/routes.py:1644]`
— **not** unauthenticated like `/platform/capabilities` `[SOURCE :1436-1443]`. The capabilities
manifest is product identity already in the README; an AgBOM names the fleet, the risk tiers and
the rails, which is a map of what to attack. Served as `application/vnd.cyclonedx+json`.

**Other files:**

| File | Change |
|---|---|
| `backend/src/app/api/schemas.py` | No response model — the body is a CycloneDX document, so return `JSONResponse` like `stack_sbom` does `[SOURCE backend/src/app/api/routes.py:1658-1664]`, or the FastAPI model will flatten the properties arrays |
| `backend/src/app/platform/sbom.py:31-32` | Fix the stale "4,219 digests" prose (it is 6,367 across both lockfiles `[MEASURED]`) |
| `docs/security/threat-model.md:38` | ASI04's mitigation becomes specific: hash-pinned lockfiles with N sha256 digests, a CI OSV gate, an SBOM artifact per build, an AgBOM endpoint — **and whatever S0 finds about `uv pip install`** |
| `web/src/components/devops/PatchCheck.tsx:475-515` | The SBOM export block gains an AgBOM download beside CycloneDX and SPDX; the section prose currently frames these as package documents and needs one line saying what the third one is |
| `web/src/lib/api/client.ts:616-626` | A `fetchAgbom` beside `fetchSbom` |
| `backend/openapi.json` | Regenerated; `scripts/build_openapi.py --check` is a CI gate `[SOURCE .github/workflows/ci.yml:130-131]` |

### Schema / migration

**None.** No table, no column, no enum. Both documents are computed at request or build time from
structures that already exist. This is the cheapest of the three SOTA plans by a wide margin and
should be scheduled first for that reason.

---

## Tasks, in dependency order

* **S0 — Answer the `uv pip install` question** (the one command above). If CI resolves freshly
  against `litellm>=1.52`, **switch CI to `uv sync --frozen`** and stop. That change alone is worth
  more than the rest of this plan, and everything below it is documentation of a posture that is
  not currently true.
* **S1 — The CI SBOM job**, with a pinned uv and `uv lock --check`.
* **S2 — `agbom.py`**: models + tools + risk tiers. Nothing else. This is the demonstrable core.
* **S3 — `agbom.py`**: guardrails (with live posture), knowledge, memory, capabilities,
  dependencies graph, integrity properties.
* **S4 — Schema validation against the published CycloneDX 1.6 JSON Schema**, in the test suite,
  offline, from a vendored copy of the schema. **This is the task that settles the `type: "tool"`
  question**, and it must run in CI or the document will drift out of validity silently.
* **S5 — The route**, RBAC-gated, plus the OpenAPI regeneration.
* **S6 — The console download.**
* **S7 — Correct `threat-model.md:38` and `sbom.py`'s stale digest count.**
* **S8 — *Optional.*** Diff the CI SBOM against the previous build's artifact and fail on an
  unreviewed dependency addition. This is the control that would actually have caught the litellm
  event in a repository whose CI resolved freshly; it is also the one most likely to be noisy, so
  it ships last and behind a soft-fail first.

---

## VERIFICATION SECTION

*Everything here is a specification of what must be run. None of it has been run.*

### The endpoints, with payloads

`TOKEN` is a platform-admin or devops bearer; `API=http://127.0.0.1:8000/v1`.

**1. The AgBOM is a valid CycloneDX document with the right top-level shape.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/platform/agbom" \
  | jq '{bomFormat, specVersion, root: .metadata.component.name, components: (.components|length)}'
```

Expect exactly:

```json
{"bomFormat":"CycloneDX","specVersion":"1.6","root":"Aegis","components":<n>}
```

and the response `Content-Type: application/vnd.cyclonedx+json`.

**2. Every fleet deployment is present, with its role and its selectability.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$API/platform/agbom" \
  | jq '[.components[] | select(.type=="machine-learning-model")] | length'
```

Expect **12** — the length of `_FLEET_DECLARATION` `[SOURCE
aegis/src/aegis/gateway/routing.py:138-176]`. Then:

```bash
curl -s ... | jq '[.components[] | select(.type=="machine-learning-model")
  | {name, sel: (.properties[]|select(.name=="aegis:model:tenant-selectable")|.value)}]'
```

Expect exactly **four** with `"true"` — `gpt-4o`, `DeepSeek-V3-0324`, `Llama-3.3-70B-Instruct`,
`Llama-4-Maverick-17B-128E-Instruct-FP8` `[SOURCE :143-166]` — and **no** `CHEAP` or `VISION`
deployment among them, because those roles are reserved for the host's own safety layers and the
fleet declaration refuses the combination at import `[SOURCE :180-192]`.

**3. Every tool carries its risk tier, and the tiers match the registry.**

```bash
curl -s ... | jq '[.components[] | select(.properties[]?.name=="aegis:tool:risk")
  | {name, risk: (.properties[]|select(.name=="aegis:tool:risk")|.value)}] | sort_by(.name)'
```

Expect exactly:

```json
[{"name":"add_case_note","risk":"low"},
 {"name":"assign_request","risk":"medium"},
 {"name":"find_requests","risk":"low"},
 {"name":"update_request_status","risk":"high"}]
```

matching `TOOL_REGISTRY` `[SOURCE backend/src/app/adapter/tools.py:694-758]`. **`find_requests`
must also carry `aegis:tool:read-only: "true"` while `add_case_note` carries `"false"`** — the
registry is explicit that read-only is asserted per tool and never inferred from the risk tier
`[SOURCE :632-638]`, and an AgBOM that derived one from the other would republish exactly the
guess that comment forbids.

**4. RBAC: an ordinary client cannot pull the AgBOM.**

```bash
curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN_CLIENT" "$API/platform/agbom"
curl -s -o /dev/null -w '%{http_code}' "$API/platform/agbom"
```

Expect `403` and `401`. Contrast `GET /platform/capabilities`, which is deliberately public
`[SOURCE backend/src/app/api/routes.py:1436-1443]` — and assert that it *stays* public, so this
change cannot break the landing page.

**5. The AgBOM and the SBOM agree about packages.**

```bash
diff <(curl -s ... "$API/stack/sbom?format=cyclonedx" | jq -S '[.components[].purl]|sort') \
     <(curl -s ... "$API/platform/agbom"             | jq -S '[.components[]|select(.purl)|.purl]|sort')
```

Expect **no output**. Two documents from one process describing different package sets is the
failure `sbom.py:25-27` guards against for its own pair, and the AgBOM must inherit that property
rather than re-derive the inventory.

**6. The CI artifact exists and is not vacuous.** After the workflow run:

```bash
jq '{specVersion, n: (.components|length)}' sbom-backend.cdx.json
```

Expect `specVersion: "1.5"` and a component count in the hundreds. **Assert a floor** (e.g.
`>= 200`) — an SBOM with three components is what a broken export produces, and it uploads
successfully.

**7. The lockfile is in step with the manifest.** `uv lock --check` in both projects exits `0`.
A non-zero exit means the SBOM artifact describes a resolve nobody runs.

### The tests, and where they go

| File | What it asserts |
|---|---|
| `backend/tests/api/test_agbom.py` **(new)** | 12 model components; 4 tool components with the exact risk tiers above; `read_only` asserted per tool and **not** derived from risk; guardrail components present; the `dependencies` graph names the agent root; `bomFormat`/`specVersion` exact |
| `backend/tests/api/test_agbom_schema.py` **(new)** | The document validates against a **vendored** CycloneDX 1.6 JSON Schema, offline. *This is the test that decides `type: "tool"` vs `type: "application"`, and the answer goes in the module docstring either way* |
| `backend/tests/api/test_agbom_rbac.py` **(new)** | 401 unauthenticated, 403 for a client role, 200 for devops; and `GET /platform/capabilities` still answers 200 with no bearer |
| `backend/tests/api/test_agbom_matches_sbom.py` **(new)** | The PURL sets are identical — one inventory pass, two documents |
| `backend/tests/test_capabilities.py` *(extend)* | Every AgBOM component whose `bom-ref` names a capability corresponds to a real `AEGIS_MODULES` entry; the existing import-check for `module_path` `[SOURCE backend/src/app/capabilities.py:13-15]` already covers the other direction |
| `backend/tests/platform/test_sbom_digest_count.py` **(new, small)** | `lockfile_digest_count()` equals the sum over **both** lockfiles, and the module docstring's number matches. *A one-line test that stops prose drifting from behaviour again* |
| `.github/workflows/ci.yml` | The `sbom` job is itself the test for A; it fails if `uv export` cannot produce the format or if `uv lock --check` fails |

Deliberately **not** written: a test per fleet deployment, a test per package, or a golden-file
comparison of the whole document. A golden file over a live inventory fails on every `uv add` and
teaches people to regenerate it without reading it.

### Frontend surfaces that must change

* `web/src/components/devops/PatchCheck.tsx:475-515` — the export block. Add the AgBOM download
  beside CycloneDX and SPDX, and one line of prose distinguishing "what this platform is made of"
  from "what this agent can do". The download handler pattern at `:512` is already there.
* `web/src/lib/api/client.ts:616-626` — `fetchAgbom`, mirroring `fetchSbom`.
* `web/src/lib/portal.ts:248` — the DevOps nav hint currently reads `'SBOM'`; it now covers two
  documents.
* `backend/openapi.json` regenerated, then `npx tsc --noEmit` in `web/` — both are CI gates
  `[SOURCE .github/workflows/ci.yml:130-131, :174-176]`.

---

## The demo this earns

> *"In March a backdoored litellm was on PyPI for forty minutes, and the FBI's FLASH in July put
> 2,500 organisations and 434,000 pipelines in scope. We pin 1.96.0, our lockfiles carry 6,367
> sha256 digests, and CI asks OSV about every installed version and fails on anything we have not
> already written a decision about. Here is the CycloneDX SBOM this build produced, generated
> from the lockfile, before deploy. And here is the thing an SBOM cannot tell you — the Agent
> Bill of Materials: four tools with their risk tiers, so you can see which ones route through
> the human gate; twelve model deployments, four of which a tenant may select and eight of which
> they may not, because two of those roles are the ones our own guardrails run on; every rail,
> with whether it is wired or merely configured. One endpoint, derived from the same structures
> the server enforces against — so it cannot describe a platform other than the one you are
> talking to."*

---

## Risks, stated plainly

1. **S0 may show that "pinned deps" does not describe CI.** That is a live supply-chain finding in
   this repository, not a documentation problem, and it outranks everything else here.
2. **The AOS AgBOM CycloneDX binding is a working draft** `[DOC
   https://aos.owasp.org/spec/inspect/extend_cyclonedx/]`. Property names may change. Namespace
   ours as `aegis:*` so a spec change is an additive mapping rather than a rewrite, and say in the
   docstring which draft was read and on what date.
3. **`type: "tool"` probably does not validate against CycloneDX 1.6.** Inference, not measurement;
   S4 settles it. Shipping an invalid document to a buyer's scanner is worse than shipping none.
4. **uv version drift between CI and developer machines is real and measured** — 0.6.7 locally
   cannot export CycloneDX at all `[MEASURED]`. Pin uv in the workflow, and expect the first
   local run of the new job to fail until developers upgrade.
5. **Two spec versions (1.5 from `uv export`, 1.6 from the endpoints) is a smell.** Tolerable if
   documented, confusing if not.
6. **The AgBOM is a map of the attack surface.** Risk tiers, reserved guardrail roles and the rail
   list are exactly what an attacker wants. It is RBAC-gated for that reason, and it must not
   drift into the public capabilities endpoint.
7. **An AgBOM that is wrong is worse than none** — the same argument `sbom.py:12-15` makes. Every
   field must be derived; the moment one is hand-maintained the document becomes a claim rather
   than a projection.

### Abandonment criteria

* **S4 shows the document cannot be made to validate** against CycloneDX 1.6 without abandoning
  the AOS property shape. Then ship it as a **plainly Aegis-namespaced JSON document** and stop
  claiming CycloneDX conformance. A non-conforming document that says so is honest; one that
  claims a spec version it fails is not.
* **The pinned uv still cannot export CycloneDX and the `cyclonedx-py environment` fallback adds
  more than a couple of minutes to CI.** Then drop S1 and keep S2–S7: the AgBOM is the novel half
  and the SBOM already exists at the endpoint.
* **Under two days remain.** Ship **S0 + S2 + S5 + S7** — the supply-chain check, models and tools
  with risk tiers, the route, and the corrected threat-model sentence. Everything else is polish
  on a demo that already lands.

---

## What this plan does **not** cover

* **Signing, in-toto or SLSA provenance.** Neither existing document is signed and neither will
  the AgBOM be. `sbom.py:29-33` already says so and that sentence stands.
* **VEX.** A CycloneDX SBOM plus a VEX document is what actually answers "are you affected by
  CVE-X"; `known_advisories.json` `[SOURCE backend/known_advisories.json]` is a repository-local
  approximation with a carefully-scoped meaning (*"a statement about the build, never about the
  risk"*). Turning it into a real VEX document is not planned here.
* **The npm/frontend dependency tree.** `web/package-lock.json` is not covered by either SBOM;
  `build_stack` parses a small curated set from `package.json` `[SOURCE
  backend/src/app/platform/stack.py:139]`. A JS SBOM is a separate job and a separate artifact.
* **Runtime AgBOM updates.** AOS describes an AgBOM that "updates in real time as agents discover
  new tools, connect to new MCP servers, or modify their own capabilities"
  `[DOC https://aos.owasp.org/spec/inspect/]`. Ours is computed per request from static
  registries — which is honest for a platform whose tool set *is* static, and would be a lie the
  moment dynamic MCP peer discovery lands. The declared MCP peer list `[SOURCE
  backend/src/app/config.py:484]` should be included as configuration, and the document must not
  imply it is discovered.
* **Per-corpus knowledge inventory.** The "Knowledge" entity wants name, description, schema,
  search type and search args per source `[DOC https://aos.owasp.org/spec/inspect/]`. Aegis models
  retrieval at the *module* level, not per corpus, so the AgBOM will carry the pipeline and not
  the documents in it. **State that in the document rather than emitting an empty array**, which a
  reader would take to mean "no knowledge sources".
* **Anything about the Langflow component palette.** Phase 11 is parked `[SOURCE
  docs/dev_new_docs_v2/phase-11-langflow.md:3-7]`; 374 third-party components would be the single
  largest section of any AgBOM, and nothing here may quietly take a dependency on that phase.
* **Attesting that the running process matches the SBOM.** `GET /stack/sbom` reads the live
  interpreter and the CI artifact reads the lockfile; nothing compares them at deploy time. That
  comparison is the actual supply-chain control, and it is S8-adjacent future work.
