/**
 * Endpoint request/response contracts — **derived from the backend, not mirrored**.
 *
 * This file used to be 775 hand-written lines restating 1,598 lines of Pydantic, and
 * the drift it invited was not hypothetical: `schemas.py` and this file have been
 * edited together in one change more than once, by hand, each time relying on somebody
 * remembering both sides. One example of what that cost, found the moment generation
 * replaced the mirror: `CreateBudgetRequest` was declared as an alias of `Budget`, so
 * the console's type for the `POST /v1/admin/budgets` body carried an `id` the server
 * has never accepted.
 *
 * Every name below is now an alias of a type generated from `backend/openapi.json`
 * (§8.7), which is itself a snapshot-tested projection of the FastAPI route table. The
 * chain — Pydantic field → OpenAPI document → `generated/schema.d.ts` → this alias →
 * the call site — has a failing test at every link, so a field that changes shape in
 * Python cannot reach the console as a silent `undefined`.
 *
 * **The aliases are kept rather than importing `components['schemas'][…]` at 60 call
 * sites**, and deliberately: they are the console's vocabulary, several of them differ
 * in name from the Python class (`Tenant` is `TenantRow`, `UsageModelRow` is
 * `UsageByModel`), and a rename on the Python side should land here, once, as a
 * one-line change under review — not as 60 broken imports.
 *
 * To change anything here, change the Python and regenerate:
 *
 *   backend/.venv/bin/python scripts/build_openapi.py
 *   cd web && npm run gen:api
 *
 * @see backend/src/app/api/schemas.py
 * @see web/src/lib/api/generated/schema.d.ts
 */

import type { components } from '@/lib/api/generated/schema'

/** Every model the published OpenAPI document declares. */
type Schemas = components['schemas']

/**
 * A response type **as it is actually sent**, with every property present.
 *
 * OpenAPI's `required` list means "the client may not omit this", and a Pydantic field
 * with a `default=` is therefore not required — on the way *in*. On the way *out* there
 * is no way in: FastAPI serialises a response model with every field set, defaults
 * included (`response_model_exclude_unset` is off everywhere in this API), so a reader
 * of a response never sees the key missing. Generating straight from `required` would
 * hand the console `points?: BurndownPoint[]` for a field the server always sends, and
 * ~60 call sites would grow an `?? []` that can never fire — noise that hides the
 * genuinely optional values behind it.
 *
 * So responses are mapped: every property is made present, recursively, with arrays and
 * tuples preserved. `| null` survives untouched, because a null a server really sends is
 * a fact about the value, not about whether the key is there.
 *
 * Request types are deliberately **not** mapped: for a body, `default=` really does mean
 * the caller may leave it out.
 */
type Sent<T> = T extends readonly unknown[]
  ? { [K in keyof T]: Sent<T[K]> }
  : T extends object
    ? { [K in keyof T]-?: Sent<T[K]> }
    : T

/** A generated type with one or more properties replaced by a narrower one. */
type Narrowed<T, N> = Omit<T, keyof N> & N

// ─────────────────────────────────────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────────────────────────────────────

/** Body for `POST /auth/login`. */
export type LoginRequest = Schemas['LoginRequest']

/**
 * The fine RBAC tier the backend derives from the coarse role + tenancy
 * (`aegis.governance.security.principal_role`). `role` collapses both admin tiers to
 * `admin`; this is the value that tells them apart, so it is a superset of `Role`.
 *
 * **One of four types on this page that are still written by hand**, with
 * `AuditOutcome`, `BudgetScope` and `BudgetWindow`. Each is a closed set the backend
 * documents in prose and types as `str` — `LoginResponse.fine_role` is
 * `fine_role: str = Field(description="'platform_admin' / 'tenant_admin' …")` — so the
 * published schema says `string` and generating from it would *widen* the console's
 * type rather than pin it. The fix is to type those four fields as literals in Python
 * (three of them live in `aegis.governance.types`, which another lane owns this
 * session); until then this narrowing is the console's own claim and is labelled as
 * one. A wrong value here fails at the call site, not silently.
 */
export type FineRole = 'platform_admin' | 'tenant_admin' | 'ai_team' | 'devops' | 'client'

/**
 * Response from `POST /auth/login`. `token` is a signed JWT (stored and sent as a
 * Bearer token); `role` scopes the served portal, `tenant_id` pins the session to its
 * tenant, `fine_role` is the admin sub-tier, and `user_id` is the JWT `sub` the
 * `/memory/*` endpoints authorise a non-admin's `user:<id>` subject against.
 */
export type LoginResponse = Narrowed<Sent<Schemas['LoginResponse']>, { fine_role: FineRole }>

// ─────────────────────────────────────────────────────────────────────────────
// The run
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Body for `POST /query`. The response is the SSE stream of `StreamEvent`s, not JSON —
 * see `sse.ts` for the reader and `@/lib/stream` for the union.
 */
export type QueryRequest = Schemas['QueryRequest']

/** Body for `GET /graph` — the current context graph for the viz. */
export type GraphResponse = Sent<Schemas['GraphResponse']>

/** Body for `POST /ml/explain`. */
export type MLExplainRequest = Schemas['MLExplainRequest']

/** Response from `POST /ml/explain` — the conformalised, SHAP-explained prediction. */
export type MLExplainResponse = Sent<Schemas['MLExplainResponse']>

/** Body for `GET /metrics` — live figures for the efficiency dashboard. */
export type MetricsResponse = Sent<Schemas['MetricsResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Approvals
// ─────────────────────────────────────────────────────────────────────────────

/** A human's decision at the approval gate. */
export type ApprovalDecision = Schemas['ApprovalDecision']

/** Body for `POST /approvals/{id}` — resolve a paused gate. */
export type ApprovalRequest = Schemas['ApprovalRequest']

/** Response from `POST /approvals/{id}`. */
export type ApprovalResponse = Sent<Schemas['ApprovalResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Governance: the audit trail, tenants, users, budgets
// ─────────────────────────────────────────────────────────────────────────────

/** One row of the append-only audit trail. */
export type AuditLogRow = Narrowed<Sent<Schemas['AuditLogRow']>, { outcome: AuditOutcome }>

/**
 * Whether an audited action was refused or ran. Derived server-side by
 * `aegis.governance.audit.classify_outcome` — there is no verdict column on the trail.
 *
 * Hand-written for the reason given on {@link FineRole}: `AuditLogRow.outcome` is typed
 * `str` in `aegis.governance.types`.
 */
export type AuditOutcome = 'blocked' | 'completed'

/** Response from `GET /audit` — the filtered trail. */
export type AuditLogResponse = Narrowed<
  Sent<Schemas['AuditLogResponse']>,
  { rows: AuditLogRow[] }
>

/** One tenant. */
export type Tenant = Sent<Schemas['TenantRow']>

/** Response from `GET /admin/tenants`. */
export type TenantsResponse = Sent<Schemas['AdminTenantsResponse']>

/** One user, as the admin surfaces list them. */
export type AdminUser = Sent<Schemas['AdminUserRow']>

/** Response from `GET /admin/users`. */
export type UsersResponse = Sent<Schemas['AdminUsersResponse']>

/**
 * Which level a budget binds at.
 *
 * Hand-written for the reason given on {@link FineRole}: `BudgetRow.scope_type` is
 * typed `str` in `aegis.governance.types`.
 */
export type BudgetScope = 'tenant' | 'user'

/**
 * The window a budget's caps reset over.
 *
 * Hand-written for the reason given on {@link FineRole}: `BudgetRow.window` is typed
 * `str` in `aegis.governance.types`.
 */
export type BudgetWindow = 'day' | 'month'

/** One configured budget, as stored. */
export type Budget = Narrowed<
  Sent<Schemas['BudgetRow']>,
  { scope_type: BudgetScope; window: BudgetWindow }
>

/** Response from `GET /admin/budgets`. */
export type BudgetsResponse = Narrowed<
  Sent<Schemas['AdminBudgetsResponse']>,
  { rows: Budget[] }
>

/**
 * Body for `POST /admin/budgets` — an upsert, keyed on `(scope_type, scope_id)`.
 *
 * **Not `Budget`.** It was declared as an alias of it until this file was generated,
 * which gave the console a body type carrying an `id` the server has never accepted.
 */
export type CreateBudgetRequest = Narrowed<
  Schemas['BudgetUpsertRequest'],
  { scope_type: BudgetScope; window?: BudgetWindow }
>

/** Body for `POST /admin/tenants`. */
export type CreateTenantRequest = Schemas['TenantCreateRequest']

/** Body for `POST /admin/users`. */
export type CreateUserRequest = Schemas['AdminUserCreateRequest']

// ─────────────────────────────────────────────────────────────────────────────
// Platform posture: the stack, patches, risk, savings, usage
// ─────────────────────────────────────────────────────────────────────────────

/** One component of the running stack, with its declared and detected versions. */
export type StackComponent = Sent<Schemas['StackComponent']>

/** Response from `GET /platform/stack`. */
export type StackResponse = Sent<Schemas['StackResponse']>

/** One dependency's patch verdict. */
export type PatchResult = Sent<Schemas['PatchResult']>

/** Response from `GET /platform/patches`. */
export type PatchCheckResponse = Sent<Schemas['PatchCheckResponse']>

/** One published advisory against one installed version. */
export type AdvisoryVulnerability = Sent<Schemas['AdvisoryVulnerability']>

/** One distribution's vulnerability verdict — a verdict, not a freshness reading. */
export type AdvisoryPackage = Sent<Schemas['AdvisoryPackage']>

/** Response from `POST /stack/advisories` — live OSV.dev verdicts. */
export type AdvisoryAuditResponse = Sent<Schemas['AdvisoryAuditResponse']>

/** One risk in the platform risk map. */
export type RiskEntry = Sent<Schemas['RiskEntry']>

/** Response from `GET /platform/risk`. */
export type RiskMapResponse = Sent<Schemas['RiskMapResponse']>

/** One line of the savings breakdown. */
export type SavingsBreakdownRow = Sent<Schemas['SavingsBreakdownRow']>

/** Response from `GET /savings` — the measured efficiency win. */
export type SavingsResponse = Sent<Schemas['SavingsResponse']>

/** Usage aggregated for one model. */
export type UsageModelRow = Sent<Schemas['UsageByModel']>

/** One point of the usage time series. */
export type UsageSeriesPoint = Sent<Schemas['UsageSeriesPoint']>

/** Response from `GET /admin/usage`. */
export type UsageResponse = Sent<Schemas['AdminUsageResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Product identity
// ─────────────────────────────────────────────────────────────────────────────

/** One Aegis module in the capabilities manifest — branded name + honest tech. */
export type AegisModuleRow = Sent<Schemas['AegisModuleRow']>

/** Response from `GET /platform/capabilities`. */
export type CapabilitiesResponse = Sent<Schemas['CapabilitiesResponse']>

/** Response from `GET /public/metrics` — the unauthenticated landing figures. */
export type PublicMetricsResponse = Sent<Schemas['PublicMetricsResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// The agent graph
// ─────────────────────────────────────────────────────────────────────────────

/** One node of the served agent topology. */
export type AgentTopologyNode = Sent<Schemas['AgentTopologyNode']>

/** One edge of the served agent topology. */
export type AgentTopologyEdge = Sent<Schemas['AgentTopologyEdge']>

/** Response from `GET /agent/topology` — the real compiled graph, never a drawing. */
export type AgentTopologyResponse = Sent<Schemas['AgentTopologyResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Voice
// ─────────────────────────────────────────────────────────────────────────────

/** One transcribed segment. */
export type VoiceSegmentRow = Sent<Schemas['VoiceSegmentRow']>

/** Response from `POST /voice/transcribe`. */
export type VoiceTranscribeResponse = Sent<Schemas['VoiceTranscribeResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Vision
// ─────────────────────────────────────────────────────────────────────────────

/** The ordered stages of one image analysis — the order **is** the security control. */
export type VisionStage = Schemas['VisionStage']

/** What one vision control decided. */
export type VisionControlOutcome = Schemas['ControlOutcome']

/** One control's verdict within a vision analysis. */
export type VisionControlReport = Sent<Schemas['ControlReport']>

/** One redacted region of an image. */
export type VisionPIIRegion = Sent<Schemas['PIIRegion']>

/** What is known about the image itself. */
export type VisionImageFacts = Sent<Schemas['ImageFacts']>

/** Token/cost usage for one vision call. */
export type VisionUsage = Sent<Schemas['VisionUsage']>

/** The injection screen's verdict on rendered text. */
export type VisionScreenVerdict = Sent<Schemas['ScreenVerdict']>

/** The output rail's verdict on the model's answer. */
export type VisionOutputRailVerdict = Sent<Schemas['OutputRailVerdict']>

/** One complete image analysis, controls included. */
export type VisionAnalysis = Sent<Schemas['VisionAnalysis']>

/** Body for `POST /vision/analyse`. */
export type VisionAnalyseRequest = Schemas['VisionAnalyseRequest']

/** Response from `POST /vision/analyse`. */
export type VisionAnalyseResponse = Sent<Schemas['VisionAnalyseResponse']>

// ─────────────────────────────────────────────────────────────────────────────
// Forecasting
// ─────────────────────────────────────────────────────────────────────────────

/** How a forecast's interval was produced: calibrated on residuals, or parametric. */
export type ForecastIntervalMethod = Schemas['ForecastResult']['interval_method']

/** One observed point of the history a forecast was fitted on. */
export type ForecastSeriesPoint = Sent<Schemas['SeriesPoint']>

/** One forecast point, with its interval. */
export type ForecastHorizonPoint = Sent<Schemas['HorizonPoint']>

/** The honest backtest behind a forecast. */
export type ForecastBacktest = Sent<Schemas['BacktestReport']>

/** One candidate model's score in the selection. */
export type ForecastCandidate = Sent<Schemas['CandidateScore']>

/** A candidate that was excluded, and why. */
export type ForecastExcludedModel = Sent<Schemas['ExcludedModel']>

/** One produced forecast. */
export type ForecastResult = Sent<Schemas['ForecastResult']>

/** One point of a budget burndown. */
export type ForecastBurndownPoint = Sent<Schemas['BurndownPoint']>

/** The budget burndown a forecast implies. */
export type ForecastBurndown = Sent<Schemas['BudgetBurndown']>

/** A refusal to forecast, naming what was missing rather than inventing a line. */
export type ForecastRefusal = Sent<Schemas['ForecastRefusal']>

/** Response from `GET /forecast`. */
export type ForecastResponse = Sent<Schemas['ForecastResponse']>
