# Design — public landing page + the falcon brand mark

**Date:** 2026-08-13 · **Status:** approved, not yet implemented

Aegis has no public front door. `/` redirects straight to `/login`, so the first thing
anyone sees — a judge, a buyer, an AI repo-reader — is a sign-in form. This design adds
a public landing page at `/` and replaces the generic shield logo with a falcon mark.

Two jury levers motivate it: **Solution Articulation (15%)**, which is judged on clarity
of explanation and visuals, and **Solution Hypothesis (20%)**, which is the "importable
enterprise agentic platform" story the landing page exists to tell.

---

## 1. The brand mark

Today the mark is lucide's `ShieldHalf` glyph, used in three places
(`web/src/components/layout/Sidebar.tsx:38`, `web/src/app/login/page.tsx:82` and `:110`).
It is generic — the same icon ships in every dashboard template.

**Replacement:** a falcon head in profile, drawn as a solid silhouette. Chosen from four
candidates (falcon head, falcon in flight, heraldic spread eagle, falcon-in-shield); the
head reads as the most distinctive and the most credible for a security-flavoured
product. The stooping/front-view variants were rejected outright because a symmetric
front-view raptor reads as an aircraft.

### Construction

- One component, `web/src/components/brand/AegisMark.tsx`. Props: `size` (px, default 20)
  and `className`. No other configuration.
- A single `<path>` on a `0 0 64 64` viewBox, `fill="currentColor"` so the mark inherits
  whatever it sits on — the dark tile in the sidebar, ink on white in the footer.
- `fill-rule="evenodd"` knocks out the eye and the falcon's malar stripe, which is the
  detail that distinguishes a falcon from a generic bird of prey.
- Decorative in the lockup (`aria-hidden`), since the adjacent "Aegis" wordmark already
  carries the name. Standalone uses take a `<title>`.

### Acceptance

The mark must stay legible at **16px**, its smallest use. The current draft goes blobby
below ~20px where the eye and malar stripe merge. Implementation refines the path and
verifies with a rendered contact sheet at 16 / 24 / 64px, in the dark tile, and in the
wordmark lockup. If a single path cannot hold at 16px, ship a second simplified path
selected by size — never a smudge in the sidebar.

Also replaces the favicon.

---

## 2. The landing page

`web/src/app/page.tsx` stops redirecting and composes the sections below. One component
per section under `web/src/components/landing/`, each independently readable and none
aware of the others. A single scrolling page; anchor nav, not routes.

| Component | Content | Data source |
|---|---|---|
| `LandingHeader` | Mark + wordmark, anchor nav, CTA | auth-aware (client) |
| `Hero` | The winning sentence, two CTAs | static |
| `ModuleGrid` | The 12 modules, branded name + honest tech | **live** `GET /platform/capabilities` |
| `ArchitectureDiagram` | browser → console → backend → core → stores | static inline SVG |
| `TrustStack` | guardrails → risk gate → governance → trace | static |
| `MetricsStrip` | Measured platform figures | **live** `GET /platform/public-metrics` |
| `Roadmap` | Production-scaling milestones + dependencies | static |
| `LandingFooter` | Docs, GitHub, honest build info | static |

### The login relationship

**The login page does not change.** It already exists at `/login`, already handles
credentials, the four demo quick-ins and role-based routing. The landing only links to it.

`LandingHeader` is auth-aware: signed out the CTA reads **Login** and links to `/login`;
with a stored session it reads **Enter console** and links to `homePathFor(session.role)`.
A judge who already signed in is not bounced back to a login form. This is the only
client component among the static sections; it reads `useAuth()` and gates on `hydrated`
so it never flashes the wrong CTA on a hard refresh.

Removing the `/` → `/login` redirect does not orphan anything: `PortalGuard` redirects
unauthenticated portal visits to `/login` directly, not via `/`.

---

## 3. New public surfaces

The landing page is public but reads live data, so two endpoints must answer without a
bearer token. Both decisions are deliberate and bounded.

### `GET /platform/capabilities` → public

Drop the `require_auth` dependency. The body is the module manifest: branded names, the
tech underneath, one-line summaries, import paths and live/optional status — the same
material already published in `README.md`. It contains no tenant, user, usage or
credential data. `GET /about` is already public on identical reasoning.

### `GET /platform/public-metrics` → new, public

A curated subset rather than opening up `/metrics`, which carries absolute USD figures.

**Published (ratios and counts):** `cache_hit_rate`, `small_model_share`,
`p95_latency_ms`, `actions_approved`, `total_calls`. The module count is *not*
duplicated here — it comes from the capabilities manifest, which is the one place that
knows it.

**Withheld (stays behind auth):** `cost_saved_usd`, `baseline_cost_usd`,
`cost_per_1k_queries_usd`, the effective `routing` map, and everything per-tenant.

Rationale: the ratios carry the efficiency story without publishing the platform's cost
base, and "we saved $X" invites "on what workload?" — a question whose honest answer is
currently unflattering, since the gateway credential is unset and `total_calls` is 0.
Every field renders an honest empty state ("not yet measured") rather than a fabricated
zero, consistent with the platform-wide no-fakes rule.

### Guarding the surface

`backend/tests/api/test_public_surfaces.py` asserts:

1. Both endpoints return 200 with **no** `Authorization` header.
2. The public-metrics body contains none of the withheld field names.
3. `/metrics` still requires auth and still carries the cost fields.

Point 2 is the load-bearing one: it makes a future edit that widens the public surface
fail loudly instead of silently leaking cost data onto an unauthenticated page.

---

## 4. Constraints

Carried from the owner's standing rules and the existing console:

- **Light / white theme only.** No dark variant.
- **No card `description`s, no page subtitles** — the density sweep in `3cdfb1f` removed
  them platform-wide; the landing must not reintroduce them.
- **Nothing fabricated.** Every number ties to a real accessor; empty states are honest.
- Existing design tokens and primitives (`components/primitives/`), not a parallel system.
- `tsc` clean, `next lint` clean, backend suite green.

---

## 5. Out of scope

Marketing content beyond what the codebase can substantiate; a blog or changelog; SEO
work beyond basic metadata; any change to the login page, the portals, or the agent.
