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
 *   2. Renders the mermaid diagrams. Every module folder has a `40-diagrams.md`
 *      that is almost entirely mermaid, so a converter that emits them as code
 *      blocks throws away the most useful file in each folder.
 *   3. Vendors mermaid.min.js locally instead of pulling it from a CDN. The
 *      target machine for this project is a locked-down, sometimes-offline
 *      Windows box; a CDN <script> tag would silently leave every diagram blank
 *      exactly when it matters.
 *
 * Usage:  node scripts/build-teaching-html.mjs [--open]
 * Output: docs/teaching/<...>.html  +  docs/teaching/index.html
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

/* ── the reading order, mirroring docs/teaching/README.md ─────────────────── */

const ORDER = [
  ['00-foundations', 'Foundations', 'Start here — the vocabulary everything assumes'],
  ['guardrails', 'Guardrails', 'What decides whether input is allowed in'],
  ['retrieval', 'Retrieval', 'Finding the evidence to answer with'],
  ['agent', 'Agent', 'Plan, decide, act, self-repair'],
  ['gateway', 'Gateway', 'Every model call funnels through here'],
  ['memory', 'Memory', 'What survives between turns and sessions'],
  ['data', 'Data', 'The portable ORM foundation'],
  ['ml', 'ML', 'Calibrated prediction as evidence'],
  ['governance', 'Governance', 'Tenants, budgets, row-level security'],
  ['observability', 'Observability', 'The glass box'],
  ['evals-ops', 'Evals & Ops', 'Measuring quality, gating change'],
  ['media', 'Media', 'The payload + rail seam'],
  ['voice', 'Voice', 'Speech in'],
  ['vision', 'Vision', 'Images in'],
  ['forecast', 'Forecast', 'Time series'],
  ['core', 'Core', 'The Module Contract'],
]

// Three files per module. The old six-file split (concepts / theory / in-aegis /
// deep-dive) explained the same idea in three places and taught it in none, so those
// four are merged into one guide — see docs/teaching/STYLE.md. The legacy stems stay
// mapped so a folder mid-migration still renders with real labels.
const FILE_LABEL = {
  '10-guide': 'Guide',
  '40-diagrams': 'Diagrams',
  '50-interview': 'Interview',
  '00-concepts': 'Concepts',
  '10-theory': 'Theory',
  '20-in-aegis': 'In Aegis',
  '30-deep-dive': 'Deep dive',
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

function sidebar(currentRel) {
  const parts = []
  for (const [dir, label, blurb] of ORDER) {
    const dirPath = join(TEACHING, dir)
    if (!existsSync(dirPath)) continue
    const open = currentRel.startsWith(dir + sep) || currentRel.startsWith(dir + '/')
    parts.push(
      `<details class="mod"${open ? ' open' : ''}>` +
        `<summary><span class="mod-name">${label}</span>` +
        `<span class="mod-blurb">${blurb}</span></summary><ul>`,
    )
    parts.push('__FILES__' + dir + '__')
    parts.push('</ul></details>')
  }
  return parts.join('\n')
}

async function fileListFor(dir, currentRel, depth) {
  const dirPath = join(TEACHING, dir)
  const names = (await readdir(dirPath)).filter((n) => n.endsWith('.md')).sort()
  const up = '../'.repeat(depth)
  return names
    .map((n) => {
      const stem = n.replace(/\.md$/, '')
      const rel = `${dir}/${stem}.html`
      const here = currentRel === `${dir}/${stem}.md`
      const label = FILE_LABEL[stem] ?? stem.replace(/^\d+-/, '').replace(/-/g, ' ')
      return `<li><a class="${here ? 'here' : ''}" href="${up}${rel}">${label}</a></li>`
    })
    .join('\n')
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

function shell({ title, sidebarHtml, body, depth, srcRel }) {
  const up = '../'.repeat(depth)
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
<p class="src">Source: <code>docs/teaching/${srcRel}</code> — the Markdown is the source of
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

  const files = (await walk(TEACHING)).sort()
  const sidebarTemplate = sidebar('')

  let count = 0
  let diagrams = 0

  for (const abs of files) {
    const srcRel = relative(TEACHING, abs).split(sep).join('/')
    const depth = srcRel.split('/').length - 1

    const md = await readFile(abs, 'utf8')
    const { stripped, blocks } = extractMermaid(md)
    diagrams += blocks.length

    let html = marked.parse(stripped, { mangle: false, headerIds: true })
    html = restoreMermaid(html, blocks)
    html = rewriteLinks(html)

    // Build this page's sidebar with the current file marked.
    let sb = sidebar(srcRel)
    for (const [dir] of ORDER) {
      if (!existsSync(join(TEACHING, dir))) continue
      sb = sb.replace(`__FILES__${dir}__`, await fileListFor(dir, srcRel, depth))
    }

    const title =
      (md.match(/^#\s+(.+)$/m)?.[1] ?? basename(srcRel, '.md')).replace(/[*`_]/g, '')

    await writeFile(abs.replace(/\.md$/, '.html'), shell({
      title, sidebarHtml: sb, body: html, depth, srcRel,
    }))
    count += 1
  }

  // Landing page — reuse README.md's rendering if present, else a plain index.
  const readme = join(TEACHING, 'README.md')
  if (existsSync(readme)) {
    const { stripped, blocks } = extractMermaid(await readFile(readme, 'utf8'))
    let html = restoreMermaid(marked.parse(stripped), blocks)
    html = rewriteLinks(html)
    let sb = sidebar('')
    for (const [dir] of ORDER) {
      if (!existsSync(join(TEACHING, dir))) continue
      sb = sb.replace(`__FILES__${dir}__`, await fileListFor(dir, '', 0))
    }
    await writeFile(
      join(TEACHING, 'index.html'),
      shell({ title: 'Aegis — zero to mastery', sidebarHtml: sb, body: html, depth: 0, srcRel: 'README.md' }),
    )
  }

  console.log(`✓ ${count} pages + index.html   (${diagrams} mermaid diagrams)`)
  console.log(`  open: docs/teaching/index.html`)
}

await main()
