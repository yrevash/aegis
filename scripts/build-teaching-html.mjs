#!/usr/bin/env node
/**
 * Render docs/teaching/ to clickable HTML alongside the Markdown.
 *
 * The .md files stay exactly where they are and are never modified — this only
 * *adds* a .html sibling for each one, so the source of truth remains the
 * Markdown and the HTML is a disposable, regenerable view.
 *
 * Three things it does that a naive md->html pass does not:
 *
 *   1. Rewrites inter-document links from `.md` to `.html`, so the whole course
 *      is clickable end to end rather than dead-ending on the first link.
 *   2. Renders the mermaid diagrams. Every module guide carries one, so a
 *      converter that emits them as code blocks throws away the single most
 *      useful thing on the page.
 *   3. Vendors mermaid.min.js locally instead of pulling it from a CDN. The
 *      target machine for this project is a locked-down, sometimes-offline
 *      Windows box; a CDN <script> tag would silently leave every diagram blank
 *      exactly when it matters.
 *
 * ## Scope: the whole `docs/` tree, not just `docs/teaching/`
 *
 * It used to render `docs/teaching/` alone, and the index it wrote linked out to
 * `../module/MODULE_REFERENCE.html`, `../compliance/README.html` and three more
 * siblings that were never generated — five dead links on the front page of the
 * course. Rendering only part of a cross-linked tree cannot work: every `.md`
 * link is rewritten to `.html`, so any document left unconverted becomes a 404
 * the moment something points at it.
 *
 * So the walk covers `docs/**` plus the root-level Markdown (README, INSTALL,
 * DESIGN, AGENTS, SKILL), and `rewriteLinks` now has a real target for every
 * link it rewrites.
 *
 * Usage:  node scripts/build-teaching-html.mjs [--open]
 * Output: a .html sibling for every .md under docs/ and the repo root,
 *         plus docs/teaching/index.html as the course front page.
 */

import { readdir, readFile, writeFile, copyFile, mkdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join, relative, dirname, basename, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const TEACHING = join(ROOT, 'docs', 'teaching')
const WEB_MODULES = join(ROOT, 'web', 'node_modules')

// marked and mermaid already live in web/node_modules — this script deliberately
// adds no new dependency to the repo.
const require = createRequire(join(WEB_MODULES, 'noop.js'))
let marked
try {
  ;({ marked } = require('marked'))
} catch {
  console.error(
    'Could not load `marked` from web/node_modules.\n' +
      'Run `npm install` in web/ first — this script reuses the console\'s deps\n' +
      'rather than adding its own.',
  )
  process.exit(1)
}

/* ── the reading order ────────────────────────────────────────────────────────
 *
 * `docs/teaching/` is flat — one file per module — and this list is the only
 * thing that gives it an order. It is written down rather than derived because
 * alphabetical is not a curriculum: `agent.md` is not where a beginner starts,
 * and `core.md` reads as trivia until you have seen three modules that depend on
 * it. A file present on disk but missing from this list still renders and still
 * gets a page; it simply falls into "Everything else" at the bottom, so adding a
 * module can never make it unreachable.
 */

const SECTIONS = [
  {
    label: 'Start here',
    blurb: 'What Aegis is, and the vocabulary the rest assumes',
    items: ['core', 'data', 'pipelines'],
  },
  {
    label: 'Walk a portal',
    blurb: 'Every screen of every persona, explained for a demo',
    items: [
      'persona-platform-admin',
      'persona-tenant-admin',
      'persona-ai-team',
      'persona-client',
    ],
  },
  {
    label: 'The safety perimeter',
    blurb: 'What decides whether anything is allowed in or out',
    items: ['guardrails', 'security', 'redteam', 'conformance', 'governance'],
  },
  {
    label: 'Answering a question',
    blurb: 'Retrieve, plan, decide, act, remember',
    items: ['retrieval', 'agent', 'memory', 'skills', 'runs'],
  },
  {
    label: 'Getting knowledge in',
    blurb: 'Documents to chunks to a graph',
    items: ['ingestion', 'jobs'],
  },
  {
    label: 'Models and money',
    blurb: 'Every model call funnels through one chokepoint',
    items: ['gateway', 'ml', 'forecast'],
  },
  {
    label: 'Proving it works',
    blurb: 'Measuring quality and gating change',
    items: ['evals', 'ops', 'observability', 'analytics', 'reports'],
  },
  {
    label: 'Other input channels',
    blurb: 'Images, speech, the open web',
    items: ['media', 'vision', 'voice', 'websearch'],
  },
  {
    label: 'Operating it',
    blurb: 'The knobs and the database',
    items: ['settings', 'dbadmin'],
  },
  {
    label: 'Beyond the course',
    blurb: 'Architecture, compliance, installation',
    items: [
      '../architecture/system-architecture',
      '../module/MODULE_REFERENCE',
      '../compliance/README',
      '../security/overview',
      '../install/README',
    ],
  },
]

/** Human labels for the pages whose stem does not read as a title. */
const LABEL = {
  'persona-platform-admin': 'Platform admin',
  'persona-tenant-admin': 'Tenant admin',
  'persona-ai-team': 'AI team',
  'persona-client': 'Client',
  '../architecture/system-architecture': 'System architecture',
  '../module/MODULE_REFERENCE': 'Module reference',
  '../compliance/README': 'Compliance position',
  '../security/overview': 'Security overview',
  '../install/README': 'Install runbook',
}

/* ── walk ─────────────────────────────────────────────────────────────────── */

async function walk(dir) {
  const out = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...(await walk(full)))
    else if (entry.name.endsWith('.md')) out.push(full)
  }
  return out
}

/* ── markdown -> html ─────────────────────────────────────────────────────── */

// Pull ```mermaid fences out BEFORE marked sees them, then put them back as
// <pre class="mermaid"> afterwards. Doing it this way (rather than via a custom
// renderer) keeps the diagram source byte-exact — marked would otherwise
// entity-escape the arrows and quotes that mermaid needs.
function extractMermaid(md) {
  const blocks = []
  const stripped = md.replace(/```mermaid\r?\n([\s\S]*?)```/g, (_, body) => {
    blocks.push(body)
    return `\n\nMERMAIDPLACEHOLDER${blocks.length - 1}\n\n`
  })
  return { stripped, blocks }
}

function restoreMermaid(html, blocks) {
  return html.replace(/<p>MERMAIDPLACEHOLDER(\d+)<\/p>/g, (_, i) => {
    const src = blocks[Number(i)]
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    return `<pre class="mermaid">${src}</pre>`
  })
}

/** Rewrite `foo.md` / `../x/foo.md#frag` links to their .html siblings. */
function rewriteLinks(html) {
  return html.replace(
    /(href=")([^"]+?)\.md(#[^"]*)?(")/g,
    (_, a, path, frag, b) => `${a}${path}.html${frag ?? ''}${b}`,
  )
}

/* ── page shell ───────────────────────────────────────────────────────────── */

function sidebar(currentStem) {
  const parts = []
  for (const { label, blurb, items } of SECTIONS) {
    const present = items.filter((stem) => pageExists(stem))
    if (present.length === 0) continue
    const open = present.includes(currentStem)
    parts.push(
      `<details class="mod"${open ? ' open' : ''}>` +
        `<summary><span class="mod-name">${label}</span>` +
        `<span class="mod-blurb">${blurb}</span></summary><ul>`,
    )
    for (const stem of present) {
      const here = stem === currentStem
      parts.push(
        `<li><a class="${here ? 'here' : ''}" href="__UP__${stem}.html">` +
          `${labelFor(stem)}</a></li>`,
      )
    }
    parts.push('</ul></details>')
  }

  // Anything on disk that no section claims. A module added tomorrow is reachable
  // today, in the wrong place rather than nowhere.
  const claimed = new Set(SECTIONS.flatMap((s) => s.items))
  const orphans = TEACHING_STEMS.filter((s) => !claimed.has(s) && s !== 'README')
  if (orphans.length > 0) {
    parts.push(
      '<details class="mod"><summary><span class="mod-name">Everything else</span>' +
        '<span class="mod-blurb">On disk, not yet placed in the reading order' +
        '</span></summary><ul>',
    )
    for (const stem of orphans) {
      const here = stem === currentStem
      parts.push(
        `<li><a class="${here ? 'here' : ''}" href="__UP__${stem}.html">` +
          `${labelFor(stem)}</a></li>`,
      )
    }
    parts.push('</ul></details>')
  }
  return parts.join('\n')
}

/** Every `.md` stem that exists directly in docs/teaching/, sorted. */
let TEACHING_STEMS = []

/** Whether a sidebar entry names a document that was actually rendered. */
function pageExists(stem) {
  if (stem.startsWith('../')) return existsSync(join(TEACHING, `${stem}.md`))
  return TEACHING_STEMS.includes(stem)
}

/** The label to print for a sidebar entry. */
function labelFor(stem) {
  if (LABEL[stem]) return LABEL[stem]
  const base = stem.split('/').pop()
  return base.charAt(0).toUpperCase() + base.slice(1).replace(/-/g, ' ')
}

const CSS = `
:root{--bg:#f9f9fa;--card:#fff;--ink:#101828;--muted:#667085;--line:#e4e7ec;
--accent:#1570ef;--code-bg:#f6f8fa;--sidebar:#fff}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{display:flex;min-height:100vh;align-items:flex-start}
aside{width:290px;flex:0 0 290px;background:var(--sidebar);border-right:1px solid var(--line);
padding:22px 0 60px;position:sticky;top:0;height:100vh;overflow-y:auto}
aside .brand{display:block;padding:0 20px 16px;font-weight:700;font-size:1.05rem;
letter-spacing:-.02em;color:var(--ink);text-decoration:none}
aside .brand small{display:block;font-weight:400;font-size:.72rem;color:var(--muted);
letter-spacing:.04em;text-transform:uppercase;margin-top:3px}
.mod{border-top:1px solid var(--line)}
.mod summary{cursor:pointer;padding:10px 20px;list-style:none;user-select:none}
.mod summary::-webkit-details-marker{display:none}
.mod summary:hover{background:#f2f4f7}
.mod-name{font-weight:600;font-size:.86rem}
.mod-blurb{display:block;font-size:.72rem;color:var(--muted);margin-top:2px;line-height:1.35}
.mod ul{list-style:none;margin:0;padding:0 0 8px}
.mod li a{display:block;padding:5px 20px 5px 32px;font-size:.82rem;color:var(--muted);
text-decoration:none;border-left:2px solid transparent}
.mod li a:hover{color:var(--ink);background:#f6f8fa}
.mod li a.here{color:var(--accent);border-left-color:var(--accent);font-weight:600;background:#eff6ff}
main{flex:1;min-width:0;padding:44px 56px 120px;max-width:900px}
main h1{font-size:2.1rem;letter-spacing:-.025em;line-height:1.2;margin:0 0 .6em}
main h2{font-size:1.42rem;letter-spacing:-.02em;margin:2.2em 0 .7em;
padding-bottom:.3em;border-bottom:1px solid var(--line)}
main h3{font-size:1.1rem;margin:1.8em 0 .5em}
main p{margin:0 0 1.05em}
main a{color:var(--accent);text-decoration:none}
main a:hover{text-decoration:underline}
main ul,main ol{margin:0 0 1.1em;padding-left:1.4em}
main li{margin:.3em 0}
main code{background:var(--code-bg);border:1px solid var(--line);border-radius:4px;
padding:.12em .38em;font:0.86em ui-monospace,SFMono-Regular,Menlo,monospace}
main pre{background:var(--code-bg);border:1px solid var(--line);border-radius:10px;
padding:15px 17px;overflow-x:auto;margin:0 0 1.3em}
main pre code{background:none;border:0;padding:0;font-size:.84rem;line-height:1.55}
main blockquote{margin:0 0 1.2em;padding:.1em 0 .1em 1.1em;border-left:3px solid var(--accent);
color:var(--muted)}
main table{border-collapse:collapse;width:100%;margin:0 0 1.4em;font-size:.9rem;display:block;
overflow-x:auto}
main th,main td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
main th{background:#f2f4f7;font-weight:600}
main tr:nth-child(even) td{background:#fcfcfd}
main hr{border:0;border-top:1px solid var(--line);margin:2.4em 0}
pre.mermaid{background:var(--card);border:1px solid var(--line);text-align:center;
padding:20px;line-height:1.2}
.nav{display:flex;justify-content:space-between;gap:16px;margin-top:56px;
padding-top:22px;border-top:1px solid var(--line);font-size:.88rem}
.src{margin-top:14px;font-size:.76rem;color:var(--muted)}
@media (max-width:900px){aside{display:none}main{padding:26px 20px 80px}}
`

function shell({ title, sidebarHtml, body, assetPrefix, srcRel }) {
  // The path back to docs/teaching/ from wherever this page lives. Computed by the
  // caller with `relative()` rather than counted from the slash depth: pages now
  // render all over docs/ and at the repo root, so "how many levels up" is not the
  // same question as "how do I reach the course folder from here".
  const up = assetPrefix
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title} · Aegis teaching</title>
<style>${CSS}</style>
</head>
<body>
<div class="wrap">
<aside>
  <a class="brand" href="${up}index.html">Aegis<small>zero to mastery</small></a>
  ${sidebarHtml}
</aside>
<main>
${body}
<p class="src">Source: <code>${srcRel}</code> — the Markdown is the source of
truth; this page is generated by <code>scripts/build-teaching-html.mjs</code>.</p>
</main>
</div>
<script src="${up}_assets/mermaid.min.js"></script>
<script>
  if (window.mermaid) {
    mermaid.initialize({
      startOnLoad: true, theme: 'base', securityLevel: 'loose',
      fontFamily: 'Inter, -apple-system, sans-serif',
      themeVariables: {
        background:'#ffffff', primaryColor:'#ffffff', primaryTextColor:'#101828',
        primaryBorderColor:'#e4e7ec', lineColor:'#98a2b3', secondaryColor:'#f9f9fa',
        tertiaryColor:'#f2f4f7', clusterBkg:'#f9f9fa', clusterBorder:'#e4e7ec', fontSize:'13px'
      },
      flowchart:{ curve:'basis', padding:14, useMaxWidth:true }
    })
  } else {
    document.querySelectorAll('pre.mermaid').forEach(function (el) {
      el.insertAdjacentHTML('beforebegin',
        '<p style="color:#b42318;font-size:.85rem">Diagram not rendered — ' +
        'mermaid.min.js missing. Re-run scripts/build-teaching-html.mjs.</p>')
    })
  }
</script>
</body>
</html>`
}

/* ── build ────────────────────────────────────────────────────────────────── */

async function main() {
  if (!existsSync(TEACHING)) {
    console.error(`No docs/teaching at ${TEACHING}`)
    process.exit(1)
  }

  // Vendor mermaid locally so diagrams render with no network.
  const assets = join(TEACHING, '_assets')
  await mkdir(assets, { recursive: true })
  const mermaidSrc = join(WEB_MODULES, 'mermaid', 'dist', 'mermaid.min.js')
  if (existsSync(mermaidSrc)) {
    await copyFile(mermaidSrc, join(assets, 'mermaid.min.js'))
  } else {
    console.warn('! mermaid.min.js not found in web/node_modules — diagrams will not render')
  }

  // Every .md under docs/, plus the root-level ones the docs link back to. A
  // partially-converted tree is a broken tree: `rewriteLinks` turns every .md
  // href into .html whether or not the target was rendered.
  const DOCS = join(ROOT, 'docs')
  // Root-level and per-package Markdown. The package READMEs are here because
  // docs/ links *out* to them — README.md alone points at four — and a link this
  // script rewrote to .html with nothing behind it is worse than the .md it
  // replaced. Every target of a rewritten link must be a target that exists.
  const OUTSIDE_DOCS = [
    'README.md',
    'INSTALL.md',
    'DESIGN.md',
    'AGENTS.md',
    'SKILL.md',
    'CHANGELOG.md',
    'aegis/README.md',
    'aegis/PUBLIC.md',
    'backend/README.md',
    'web/README.md',
  ]
    .map((n) => join(ROOT, n))
    .filter((f) => existsSync(f))
  const files = [...(await walk(DOCS)), ...OUTSIDE_DOCS].sort()

  TEACHING_STEMS = files
    .filter((f) => dirname(f) === TEACHING)
    .map((f) => basename(f, '.md'))
    .sort()

  let count = 0
  let diagrams = 0

  for (const abs of files) {
    const md = await readFile(abs, 'utf8')
    const { stripped, blocks } = extractMermaid(md)
    diagrams += blocks.length

    let html = marked.parse(stripped, { mangle: false, headerIds: true })
    html = restoreMermaid(html, blocks)
    html = rewriteLinks(html)

    // How far this page sits from docs/teaching/, so the sidebar's links and the
    // mermaid <script> resolve from wherever it lives.
    const up = relative(dirname(abs), TEACHING).split(sep).join('/')
    const prefix = up === '' ? '' : `${up}/`
    const stem = dirname(abs) === TEACHING ? basename(abs, '.md') : null
    const sb = sidebar(stem ?? '').replaceAll('__UP__', prefix)

    const title =
      (md.match(/^#\s+(.+)$/m)?.[1] ?? basename(abs, '.md')).replace(/[*`_]/g, '')

    await writeFile(
      abs.replace(/\.md$/, '.html'),
      shell({
        title,
        sidebarHtml: sb,
        body: html,
        assetPrefix: prefix,
        srcRel: relative(ROOT, abs).split(sep).join('/'),
      }),
    )
    count += 1
  }

  // The course front page is README.md rendered at docs/teaching/index.html, so
  // the sidebar's `brand` link and every relative href resolve from that folder.
  const readme = join(TEACHING, 'README.md')
  if (existsSync(readme)) {
    const { stripped, blocks } = extractMermaid(await readFile(readme, 'utf8'))
    let html = restoreMermaid(marked.parse(stripped), blocks)
    html = rewriteLinks(html)
    await writeFile(
      join(TEACHING, 'index.html'),
      shell({
        title: 'Aegis — zero to mastery',
        sidebarHtml: sidebar('').replaceAll('__UP__', ''),
        body: html,
        assetPrefix: '',
        srcRel: 'docs/teaching/README.md',
      }),
    )
  }

  console.log(`\u2713 ${count} pages + index.html   (${diagrams} mermaid diagrams)`)
  console.log(`  open: docs/teaching/index.html`)
}

await main()
