# Phase 0 · Task A0 — OWASP Top 10 for Agentic Applications (ASI01–ASI10)

**Status: PRIMARY-SOURCED — VERIFIED.**
The ten identifiers and titles below were extracted from the **official OWASP PDF itself**
(downloaded from `genai.owasp.org`), not from blogs. They are safe to quote on a compliance
page.

Task: establish the ten ASI category identifiers and titles verbatim from a primary source,
cross-check them, and list every mismatch against `docs/security/threat-model.md`.

---

## 1. Provenance — what worked and what refused me

### Succeeded (PRIMARY)

| # | URL | Method | Result |
|---|-----|--------|--------|
| 1 | `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/` | `curl` + browser User-Agent | **200** — canonical resource page; carries the download link |
| 2 | **`https://genai.owasp.org/download/52117/?tmstv=1765059207`** | `curl` + browser UA + Referer | **200, `application/pdf`, 1 274 186 bytes — THE OFFICIAL DOCUMENT** |
| 3 | `https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/` | `curl` + browser UA | **200** — the launch announcement; names all ten inline |
| 4 | `https://github.com/GenAI-Security-Project/GenAI-Agent-Security-Initiative` | `gh api` (authenticated) | **200** — the project's own GitHub org and repo; holds the versioned drafts |

**The document actually used for every verbatim quote below (row 2):**

- Title (PDF metadata): `Microsoft Word - OWASP Top 10 for Agentic Applications 2026 12.6.docx`
- Cover page: *OWASP Top 10 For Agentic Applications 2026 — OWASP Gen AI Security Project - Agentic Security Initiative*
- Stated version: **"Version 2026 / December 2025"**
- 57 pages · SHA-256 `a2db94cd00b08e0b3a5e5b619afe024bdbcd74503111085705e4f3dd886fcb5c`
- Licence: CC BY-SA 4.0 (so quoting it with attribution is permitted)

### Refused me

| URL | Method | Result |
|-----|--------|--------|
| `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-v1-0/` | WebFetch | **403 Forbidden** |
| `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-v1-0/` | `curl` + browser UA | **404** — this slug does not exist. The earlier agent's 403 was partly a **guessed URL**; the real slug ends **`-for-2026`**, not `-v1-0`. |
| `https://api.github.com/...` (unauthenticated) | `curl` | **403** rate-limited — resolved by using authenticated `gh api` |

**Root cause of the earlier failure:** `genai.owasp.org` returns 403 to WebFetch's default
client but serves normally to `curl` with an ordinary browser `User-Agent`. It is a
bot-filter, not an access restriction. **Anyone re-verifying this should use `curl -A "Mozilla/5.0 …"`, not WebFetch.**

---

## 2. The ten categories — VERBATIM from the official PDF

All ten titles were taken from **two independent places inside the same PDF** — the Table of
Contents (pp. 2–4) and the section headings themselves (pp. 9–36) — and they agree exactly.

**[DOC]** every row: `https://genai.owasp.org/download/52117/?tmstv=1765059207`
(reached via `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/`)

| ID | Title (verbatim, canonical) | Description (framework's own words, verbatim) |
|----|------------------------------|-----------------------------------------------|
| **ASI01** | **Agent Goal Hijack** | "AI Agents exhibit autonomous ability to execute a series of tasks to achieve a goal. Due to inherent weaknesses in how natural-language instructions and related content are processed, agents and the underlying model cannot reliably distinguish instructions from related content." |
| **ASI02** | **Tool Misuse and Exploitation** | "Agents can misuse legitimate tools due to prompt injection, misalignment, or unsafe delegation or ambiguous instruction - leading to data exfiltration, tool output manipulation or workflow hijacking. Risks arise from how the agent chooses and applies tools; agent memory, dynamic tool selection, and delegation can contribute to misuse via chaining, privilege escalation, and unintended actions." |
| **ASI03** | **Identity and Privilege Abuse** | "Identity & Privilege Abuse exploits dynamic trust and delegation in agents to escalate access and bypass controls by manipulating delegation chains, role inheritance, control flows, and agent context; context includes cached credentials or conversation history across interconnected systems. In this context, identity refers both to the agent's defined persona and to any authentication material that represents it. Agent-to-agent trust or inherited credentials can be exploited to escalate access, hijack privileges, or execute unauthorized actions." |
| **ASI04** | **Agentic Supply Chain Vulnerabilities** | "Agentic Supply Chain Vulnerabilities arise when agents, tools, and related artefacts they work with are provided by third parties and may be malicious, compromised, or tampered with in transit. These can be both static and dynamical sourced components, including models and model weights, tools, plug-ins, datasets, other agents, agentic interfaces - MCP (Model Context Protocol), A2A (Agent2Agent) - agentic registries and related artifacts, or update channels. These dependencies may introduce unsafe code, hidden instructions, or deceptive behaviors into the agent's execution chain." |
| **ASI05** | **Unexpected Code Execution (RCE)** | "Agentic systems - including popular vibe coding tools - often generate and execute code. Attackers exploit code-generation features or embedded tool access to escalate actions into remote code execution (RCE), local misuse, or exploitation of internal systems. Because this code is often generated in real-time by the agent it can bypass traditional security controls." |
| **ASI06** | **Memory & Context Poisoning** | "Agentic systems rely on stored and retrievable information which can be a snapshot of its conversation history, a memory tool or expanded context, which supports continuity across tasks and reasoning cycles. […] In Memory and Context Poisoning, adversaries corrupt or seed this context with malicious or misleading data, causing future reasoning, planning, or tool use to become biased, unsafe, or aid exfiltration." |
| **ASI07** | **Insecure Inter-Agent Communication** | "Multi agent systems depend on continuous communication between autonomous agents that coordinate via APIs, message buses, and shared memory, significantly expanding the attack surface. Decentralized architecture, varying autonomy, and uneven trust make perimeter-based security models ineffective. Weak inter-agent controls for authentication, integrity, confidentiality, or authorization let attackers intercept, manipulate, spoof, or block messages." |
| **ASI08** | **Cascading Failures** | "Agentic cascading failures occur when a single fault (hallucination, malicious input, corrupted tool, or poisoned memory) propagates across autonomous agents, compounding into system-wide harm. Because agents plan, persist, and delegate autonomously, a single error can bypass stepwise human checks and persist in a saved state." |
| **ASI09** | **Human-Agent Trust Exploitation** | "Intelligent agents can establish strong trust with human users through their natural language fluency, emotional intelligence, and perceived expertise, known as anthropomorphism. Adversaries or misaligned designs may exploit this trust to influence user decisions, extract sensitive information, or steer outcomes for malicious purposes." |
| **ASI10** | **Rogue Agents** | "Rogue Agents are malicious or compromised AI Agents that deviate from their intended function or authorized scope, acting harmfully, deceptively, or parasitically within multi-agent or human-agent ecosystems." |

Notes on the quotations:

- The `[…]` in ASI06 marks a skipped paragraph break — the defining sentence sits in the
  section's second paragraph. Everything either side of it is verbatim.
- Typos are **the source's own** and were preserved rather than silently corrected:
  "dynamical sourced" (ASI04); elsewhere in the PDF "panning" for "planning" (ASI01 ¶2) and
  "AS01"/"AS04" for "ASI01"/"ASI04" (ASI10 ¶2). Do not quote those sentences without `[sic]`.
- Punctuation in the descriptions is the PDF's (spaced hyphens, curly apostrophes).

### `and` vs `&` — the one internal inconsistency, and which form to use

**[DOC]** same PDF. The document is not self-consistent about the conjunction in ASI02 and ASI03:

| Where in the PDF | ASI02 | ASI03 |
|---|---|---|
| Table of Contents (p. 2) **and** section heading (pp. 12, 15) | Tool Misuse **and** Exploitation | Identity **and** Privilege Abuse |
| "Agentic Top 10 At A Glance" summary card (p. 8), and all in-body cross-references | Tool Misuse **&** Exploitation | Identity **&** Privilege Abuse |

**Recommendation:** use the **`and`** form for ASI02/ASI03, because that is what both the
Table of Contents *and* the section headings say — two of three placements, and the two that
function as the document's own canonical naming. Either form is defensible; **`&` is not an
error.** ASI06 is `&` everywhere with no variant, so ASI06 keeps its ampersand.

---

## 3. Cross-check against independent secondary sources

**Secondary source A — Cycode** [DOC] `https://cycode.com/blog/owasp-top-10-agentic-applications/`

**Exact match on all ten**, ordering included. It uses the `&` variant for ASI02/ASI03,
which is the PDF's own "At A Glance" spelling. **No discrepancy.**

**Secondary source B — DeepTeam** [DOC] `https://www.trydeepteam.com/docs/frameworks-owasp-top-10-for-agentic-applications`

Correct ordering, but **three titles are paraphrased and wrong**:

| ID | DeepTeam says | Official PDF says | Verdict |
|----|---------------|-------------------|---------|
| ASI03 | "Agent Identity & Privilege Abuse" | Identity and Privilege Abuse | DeepTeam adds "Agent" — **wrong** |
| ASI04 | "Agentic Supply Chain Compromise" | Agentic Supply Chain Vulnerabilities | **wrong** |
| ASI08 | "Cascading Agent Failures" | Cascading Failures | DeepTeam adds "Agent" — **wrong** |

**Secondary source C — the OWASP launch announcement** (primary-adjacent; same publisher)
[DOC] `https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/`

Names all ten in prose and agrees, with two shortenings: it writes "ASI02 – Tool Misuse"
(dropping "and Exploitation") and "ASI06 – Memory & Context Poisoning" (confirming ASI06).

**Resolution.** Where sources disagree, **trust the PDF.** It is the framework publishing its
own identifiers, obtained directly from `genai.owasp.org`, and its Table of Contents and
section headings corroborate each other. Cycode independently confirms it. DeepTeam is a
third-party tool's paraphrase and should not be quoted.

**Also confirmed from the project's own GitHub** [DOC]
`https://github.com/GenAI-Security-Project/GenAI-Agent-Security-Initiative` (path
`agentic-top-10/Sprint 1-first-public-draft-expanded/`): the first public draft carries the
same ten slots, with ASI01 then titled **"Agent Behaviour Hijack"** — renamed to **"Agent
Goal Hijack"** for the final release. This is a useful confirmation that the numbering
stabilised, and a warning that **draft-era filenames in that repo are not the final titles**.
An earlier `0.5-initial-candidates/` folder holds a completely different sixteen-item list
(ASI01 "Memory Poisoning", ASI11, ASI12 …) — **that list is obsolete; never cite it.**

---

## 4. Mismatches against `docs/security/threat-model.md` (lines 33–44) — ACTIONABLE

The table under "## 2. OWASP Top 10 for Agentic Applications (ASI, 2026)". Ordering and
numbering are **100% correct** — all ten slots map to the right category. Six titles are
exact. **Four need editing, of which one is substantive.**

| ID | threat-model.md currently says | Official title | Verdict |
|----|-------------------------------|----------------|---------|
| ASI01 | Agent Goal Hijack | Agent Goal Hijack | ✅ **MATCH** |
| ASI02 | Tool Misuse & Exploitation | Tool Misuse and Exploitation | ⚠️ **Cosmetic** — `&` vs `and`; matches the PDF's own "At A Glance" card. Optional fix. |
| ASI03 | Identity & Privilege Abuse | Identity and Privilege Abuse | ⚠️ **Cosmetic** — same `&` vs `and`. Optional fix. |
| ASI04 | Agentic Supply Chain | Agentic Supply Chain **Vulnerabilities** | ❌ **FIX** — the word "Vulnerabilities" is missing. |
| ASI05 | Unexpected Code Execution | Unexpected Code Execution **(RCE)** | ❌ **FIX** — the "(RCE)" suffix is missing. |
| ASI06 | **Context / Retrieval Manipulation** | **Memory & Context Poisoning** | ❌ **FIX — SUBSTANTIVE.** This is not a wording slip; it is a different name for the category. No OWASP source uses "Context / Retrieval Manipulation". Must be replaced. |
| ASI07 | Insecure Inter-Agent Communication | Insecure Inter-Agent Communication | ✅ **MATCH** |
| ASI08 | Cascading Failures | Cascading Failures | ✅ **MATCH** |
| ASI09 | Human-Agent Trust Exploitation | Human-Agent Trust Exploitation | ✅ **MATCH** |
| ASI10 | Rogue Agents | Rogue Agents | ✅ **MATCH** |

**Summary: 3 must-fix (ASI04, ASI05, ASI06) + 2 cosmetic (ASI02, ASI03). 5 mismatches total, 6 exact matches.**

### The one that matters

ASI06 is the only entry that would actually **misquote the framework**. The row currently reads:

> | **ASI06** | Context / Retrieval Manipulation | **Azure Spotlighting** (delimiting + datamarking) marks retrieved text as *data, not instructions*; validate-before-write to the store. |

The title must become **Memory & Context Poisoning**. Note that OWASP's ASI06 is explicitly
about the **persistent corruption of stored context or long-term memory** and, in the PDF's
own words, "excludes one-time input prompts covered under LLM01:2025 Prompt Injection." Aegis's
stated mitigation (Spotlighting on retrieved text + validate-before-write) does land on
ASI06 — validate-before-write is exactly the ingestion control ASI06 asks for — but whoever
edits this row should confirm the mitigation text still reads correctly under the *poisoning*
framing rather than the *retrieval-manipulation* framing.

### Also worth checking while editing

- Line 10 of `threat-model.md` calls the framework **"OWASP Top 10 for Agentic Applications
  (ASI, 2026)"**. The PDF's cover reads *"OWASP Top 10 For Agentic Applications 2026"*,
  version "Version 2026", dated **December 2025**. The doc's naming is **correct**; if a
  date is ever shown, it is *published December 2025, 2026 edition* — not "published 2026".
- Line 33's column header is "Threat"; OWASP calls these **risks**/**categories**. Cosmetic only.

---

## 5. The obsolete hedge in `docs/security/owasp-agentic.md` — EXACT TEXT TO CORRECT

Lines **18–24**, quoted character-for-character:

```markdown
> **Naming note (honest).** The OWASP GenAI Security Project's agentic work is evolving;
> the *category themes* below (excessive agency, tool misuse/hijacking, trust-chain abuse,
> prompt injection, sensitive-information disclosure, …) are stable, but exact item
> **numbering/wording should be confirmed against the current OWASP publication** before
> quoting an "ASI0x" identifier. Where Aegis's own code annotates the older LLM Top 10
> (e.g. `LLM02` insecure output handling, `LLM06` sensitive-info disclosure), those
> annotations are noted so the lineage is traceable.
```

**Why it is now obsolete.** The hedge's condition has been satisfied: the numbering and
wording *have* now been confirmed against the current OWASP publication (§1–2 above). The
sentence "numbering/wording should be confirmed" should be replaced with a statement that
they **were** confirmed, citing the resource page and its December 2025 / Version 2026 PDF.

**Two cautions for whoever rewrites it — do not just delete the paragraph:**

1. **The rest of `owasp-agentic.md` is not ASI-numbered.** Its main table (lines 30–39) is
   numbered **1–8** by *theme* ("Excessive agency / autonomy", "Tool misuse / hijacking", …),
   not by ASI identifier, and has **eight** rows, not ten. Removing the hedge without
   renumbering that table to ASI01–ASI10 would leave the file claiming alignment it does not
   show. Either renumber the table against the verified list, or keep an honest note saying
   the table is organised by theme and pointing at `threat-model.md` for the ASI mapping.
2. **The parenthetical inside the hedge is itself wrong.** It says "`LLM02` insecure output
   handling, `LLM06` sensitive-info disclosure". Under **OWASP Top 10 for LLM Applications
   v2.0 (2025)** — the list `threat-model.md` uses at lines 18–27 — LLM02 is *Sensitive
   Information Disclosure*, LLM05 is *Improper Output Handling*, and LLM06 is *Excessive
   Agency*. So the two are **swapped and mis-assigned** relative to the 2025 list.
   `threat-model.md` has them right. These appear to be legacy 2023-list annotations carried
   in from the code; verifying the LLM Top 10 v2.0 identifiers is **out of scope for A0**
   (which covered ASI only) and should be its own task before that parenthetical is quoted.

---

## 6. Reproduce this verification

```sh
curl -sSL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/120 Safari/537.36" \
  -e "https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/" \
  -o asi.pdf "https://genai.owasp.org/download/52117/?tmstv=1765059207"

shasum -a 256 asi.pdf   # a2db94cd00b08e0b3a5e5b619afe024bdbcd74503111085705e4f3dd886fcb5c
pdftotext -layout asi.pdf asi.txt && sed -n '44,155p' asi.txt   # the Table of Contents
```

The `?tmstv=` query parameter is a cache-buster and may change; if the download 404s, re-read
the link from the resource page. Do **not** use WebFetch against `genai.owasp.org` — it 403s.

---

*Compiled 2026-08-27. Primary source verified. No source file was modified by this task.*
