# Illustrations

Thirteen scenes from **[Storyset](https://storyset.com/)** (Freepik Company).

## Licence — and why no credit is rendered

These are **[Storyset](https://storyset.com/)** (Freepik Company) scenes. Storyset's
free licence asks for a visible, followable credit when the work is published.

**This project is a hackathon demo and is not published**, so the owner's decision
is that no attribution is rendered in the product. The credit is recorded here
instead, which is where it belongs for an unpublished build.

**If this ever ships to production, that changes.** The link goes in a visible
footer, or the scenes come out. Do not carry this file's current state into a
public deployment and assume it is settled — it is settled *for a demo*.

    Illustrations by Storyset — https://storyset.com/

## Recolouring

Each file has been recoloured from Storyset's own accent family onto the Aegis blue
ramp, so the illustrations belong to the product rather than sitting beside it:

    #547cef -> #1570ef   --blue-600   (Storyset primary, "amico"/"pana" sets)
    #5585f1 -> #1570ef   --blue-600   (Storyset primary, "rafiki" set)
    #4a80f9 -> #1570ef   --blue-600   (Storyset primary, "bro"/"cuate" sets)
    #6880c8 -> #175cd3   --blue-700
    #4262c7 -> #0b3b8f   --blue-900
    #7d9bf5 -> #60a5fa   --blue-400
    #92a9f7 -> #bfdbfe   --blue-200

Only that accent family was touched. The greys, the line work and the figures' own
tones are untouched — those are the drawing, not the brand, and repainting them
would flatten scenes that were composed with them.

## The scenes, and what each is actually about

Chosen for what they depict, not for decoration — a scene that does not describe
something Aegis really does does not belong on the page.

| file | subject | where it belongs |
|---|---|---|
| `people creating robot-amico.svg` | building an agent | landing — what the product is |
| `forming team leadership-amico.svg` | a team on one task | landing — fan-out |
| `people using robots-amico.svg` | operating agents | landing |
| `leadership-amico.svg` | the human deciding | approvals |
| `Consent-rafiki.svg` | authorising an action | approvals — the consent gate |
| `Security-pana.svg` | guardrails | guardrails / security |
| `forensic expert-pana.svg` | evidence, red team | red team |
| `software tester-amico.svg` | evals | evals / harness |
| `Sync-pana.svg`, `Sync-rafiki.svg` | orchestration | console / flow |
| `Upload-pana.svg`, `Upload-rafiki.svg` | ingestion | **documents — upload your knowledge** |
| `401 Error Unauthorized-rafiki.svg` | refused access | 401 / forbidden states |
| `No data-rafiki.svg` | nothing recorded yet | **empty states everywhere** |
| `Business growth-amico.svg` | spend and forecast | forecast / analytics |
| `Online resume-cuate.svg` | a record being read | audit / a single request |
| `Curious-rafiki.svg` | asking a question | console idle state |
| `brand guidelines-rafiki.svg` | a documented standard | settings / governance |
| `404 Error-rafiki.svg` | wrong address | **404 page** |
| `Forgot password-rafiki.svg` | locked out, recoverable | login — recovery path |
| `Visual data-rafiki.svg` | reading a chart | analytics / insights empty state |
| `Investment data-rafiki.svg` | money over time | forecast / savings empty state |
| `Presentation-rafiki.svg` | showing findings | dashboard / reports empty state |
| `FAQs-rafiki.svg` | asking a question | console idle, help |
| `Company-rafiki.svg` | an organisation | tenants / governance |
| `Mobile testing-rafiki.svg` | trying it out | simulation / harness / evals |
| `Flying phoenix-*.svg` (3) | the mark itself | **landing hero / brand** — Aegis is a phoenix |
| `Risk management-*.svg` (3) | weighing an exposure | **risk** screen |
| `Saving money-bro.svg`, `Savings-pana.svg` | money kept back | **savings** screen |
| `Cyborg-amico.svg`, `Cyborg-bro.svg` | a machine agent | agents / console |
| `Preferences-cuate.svg`, `Preferences-pana.svg` | choosing settings | settings |
| `Vault-bro.svg` | something sealed | security / secrets |
| `Server-bro.svg` | the stack | devops stack / health |
| `Business analytics-bro.svg` | reading the numbers | analytics |
| `Online document-rafiki.svg` | a document on file | documents / corpus |
| `Oops! 404 Error with a broken robot-cuate.svg` | wrong address, on brand | 404 — better than the plain 404 scene |
| `Charity-amico.svg`, `messy bun-*.svg` | — | unused; nothing they depict is true here |

**Use a scene only where it describes something the product really does.** An
illustration that decorates a screen it has nothing to do with reads as stock art,
which is worse than no illustration. Two of these are marked unused for exactly
that reason rather than forced onto a page.

## Where they are used on the pre-auth pages

The three surfaces a visitor meets before a portal — the landing page, sign-in and
the 404 — share one voice and one scene component
(`web/src/components/landing/LandingScene.tsx`), which gives each scene a real
`alt` because these are editorial images with no adjacent sentence describing
them. The console's own screens keep `components/illustration/Scene`, whose
scenes are `aria-hidden` because they always sit beside text saying the same thing.

| surface | scene | why it is true there |
|---|---|---|
| landing — fan-out | `forming team leadership-amico.svg` | a team on one task, which is what a fan-out is |
| landing — the human gate | `Consent-rafiki.svg` | a consent form being signed; the gate's sentence is a record |
| login | `Security-pana.svg` | a locked screen, which is what a sign-in form is for |
| 404 | `404 Error-rafiki.svg` | wrong address, and the code is drawn into the artwork |

**Three were rejected on sight, and the reasons are the rule working.**

* `401 Error Unauthorized-rafiki.svg` for the landing page's tenant-isolation
  section. Aegis does not answer a cross-tenant question with a 401 — it answers
  with a run that says it cannot source anything — and the artwork draws "401" at
  200 points. That section ships with no picture instead.
* `No data-rafiki.svg` for the 404. It means *nothing has been recorded yet*,
  which is an empty state; telling someone who mistyped a URL that there is no
  data answers a question they did not ask.
* `people using robots-amico.svg` for the sign-in panel. Rendered, it is a family
  on a sofa with a robot vacuum — a consumer smart-home scene, and not one frame
  of what this product does.

**And one page ships with none.** `web/src/app/error.tsx` — nothing in the set
depicts a screen that stopped rendering, and a picture chosen for the mood of a
page is the definition of stock art. It carries the failure's own digest instead.

**They only work on a light ground.** Every file carries its own near-white
background plate, so on `--rail` navy a scene reads as a pale rectangle pasted
onto the panel rather than as a drawing. Measured, not assumed — it is why the
sign-in page's left panel is the blue-tinted canvas rather than the navy it was
first drafted as, and why the landing page's one navy surface carries no scene.
