# DESIGN_REFERENCE.md — Neutral light-dashboard design system

The look the problem statement drops into on the day. Synthesized from two Figma
references the team chose (both accessible + extracted via Figma MCP). Source of
truth for the UI build. The two Figma sources (screenshots are no longer kept in the repo):
- `figma_snowui_admin.png` — **SnowUI / ByeWind** admin (`x4aT6z1dFul30bPSDyCM7L`, frame `549:8646`) — minimal, enterprise.
- `figma_saas_dashboard.png` — **SaaS Dashboard UI Kit** (`atV80NmxCOHvCjUuMxrnRj`, frame `2:60`) — friendlier, rounded.

## Direction
**Light, clean, minimal, neutral.** White cards on a near-white base, generous
whitespace, one restrained primary + a soft pastel palette for charts, Inter type,
oversized bold metric numbers. Enterprise-credible and domain-agnostic — any problem
statement drops in by swapping copy, icons, and (optionally) the single brand hue.
This is a **light reskin** of the current dark "Aegis Console."

## Color tokens
Neutral base (from SnowUI + SaaS Kit variables):
| Token | Hex | Use |
|---|---|---|
| Background/base | `#F9F9FA` | app background |
| Surface/card | `#FFFFFF` | cards |
| Card tint — blue | `#E6F1FD` | soft KPI card background |
| Card tint — purple | `#EDEEFC` | soft KPI card background |
| Text primary | `#1A1A1A` / Black-80–100% | headings, numbers |
| Text secondary | `#667085` / Black-40% | labels |
| Border | Black 4–10% (`#0000000a`–`#0000001a`) | hairline card borders |
| **Primary** | near-black `#101828` (swappable brand hue) | primary action / active nav |
| Success | `#12B76A` | positive delta |
| Danger | `#F04438` | negative delta |

Chart / secondary palette (soft pastels — SnowUI): mint `#6BE6D3`, blue `#7DBBFF`,
purple `#B899EB`, green `#71DD8C`, cyan `#A0BCE8`, indigo `#9F9FF8`. Use black/near-black
as the "primary series" line, pastels for the rest.

## Type
- **Inter** (UI + numbers). Weights: Regular 400, Semibold 600.
- Scale: 12/16, 14/20, 24/36 (semibold headings). Metric numbers are the hero — very
  large + bold, near-black.
- Secondary labels in `#667085`.

## Spacing & shape
- Spacing scale: 4 / 8 / 12 / 16 / 20 / 24 / 28 / 40 / 80.
- **Radius: 8–16px** (8 SnowUI-crisp, 16 SaaS-soft — pick 12 as the middle).
- Cards: soft diffuse shadow OR hairline black-opacity border; 20–24px padding.

## Layout
```
┌──────────┬──────────────────────────────────────┬──────────────┐
│ SIDEBAR  │ breadcrumb / search / theme / bell    │  RIGHT RAIL  │
│ ~212–240 │ ┌KPI┐┌KPI┐┌KPI┐┌KPI┐  (tinted cards)   │ Notifications│
│ logo     │ ┌ big line chart ┐┌ Traffic list ┐     │ Activities   │
│ grouped  │ ┌ bar chart ┐┌ donut + legend ┐        │ Contacts     │
│ nav      │ ...                                    │ (avatars)    │
└──────────┴──────────────────────────────────────┴──────────────┘
```
- **Left sidebar** (~212–240px): logo, grouped nav (Favorites / Dashboards / Pages),
  optional promo card, logout.
- **Main grid**: KPI card row → big chart + side list → bar + donut → more blocks.
- **Right rail** (~280px): notifications / activity / contacts feeds (avatar rows).

## Components to build
- **KPI card** — soft-tinted background, label, oversized bold number, delta chip (▲green/▼red), tiny arrow/sparkline.
- **Line chart** — solid near-black primary + dashed comparison series, soft grid, hover marker.
- **Bar chart** — pastel bars, rounded tops.
- **Donut + legend** — black primary + pastels, % legend rows.
- **Traffic/list rows** — label + thin progress bar / mini-bar + value.
- **Activity / contact rows** — avatar + primary/secondary text + timestamp.
- **Top bar** — breadcrumb, search, theme toggle, notifications, avatar.

## How this maps onto our app
Keep the information architecture (agent console, dashboard, audit, ML panels) but
reskin to this **light** system. Our functional "signal hues" (reasoning/retrieval/
gate/guardrail/ML) map onto the pastel secondary palette for the trace/graph/trust
states; everything else goes neutral-on-white. Provide a **light/dark token layer** so
we can still flip to the dark control-room if wanted.
```
```
