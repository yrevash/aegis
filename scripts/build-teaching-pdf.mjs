#!/usr/bin/env node
/**
 * Bind the whole teaching course into one printable PDF.
 *
 * `build-teaching-html.mjs` renders one page per document, which is the right
 * shape for reading on screen and the wrong shape for handing someone a book.
 * This produces the book: a title page, a contents list, then all twenty-nine
 * module documents in the course's own reading order, each starting on a fresh
 * page.
 *
 * ## Why Playwright rather than a Markdown-to-PDF converter
 *
 * The diagrams. Every module carries one mermaid block, and mermaid renders in a
 * browser — it turns the source into SVG at run time. A converter that never
 * executes JavaScript emits those blocks as code listings, which throws away the
 * single most useful thing on each page. So the pipeline is: Markdown to one HTML
 * document, open it in a real browser, wait for mermaid to finish, then print.
 *
 * Mermaid is loaded from `web/node_modules` and inlined into the document, so the
 * render needs no network and cannot silently produce a PDF full of blank frames
 * on a machine that is offline.
 *
 * ## Reading order
 *
 * Taken from `docs/teaching/README.md`'s own grouping rather than from the
 * directory listing, because alphabetical is not a curriculum. A module on disk
 * that no group claims is still included, under "Everything else", so adding a
 * document can never silently drop it from the book.
 *
 * Usage:  node scripts/build-teaching-pdf.mjs
 * Output: docs/teaching/Aegis-teaching-course.pdf
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const TEACHING = join(ROOT, 'docs', 'teaching')
const WEB_MODULES = join(ROOT, 'web', 'node_modules')
const OUT = join(TEACHING, 'Aegis-teaching-course.pdf')

const require = createRequire(join(WEB_MODULES, 'noop.js'))

let marked, chromium
try {
  ;({ marked } = require('marked'))
  ;({ chromium } = require('playwright'))
} catch (error) {
  console.error(
    'Needs `marked` and `playwright` from web/node_modules.\n' +
      'Run `npm install` in web/ first — this script adds no dependency of its own.\n' +
      String(error),
  )
  process.exit(1)
}

/* ── the course order, mirroring docs/teaching/README.md ──────────────────── */

const GROUPS = [
  ['The contract', ['core', 'data', 'pipelines']],
  ['Governance and safety', ['governance', 'guardrails', 'security', 'redteam', 'conformance', 'settings', 'dbadmin']],
  ['The agent', ['agent', 'memory', 'skills']],
  ['Knowledge', ['ingestion', 'retrieval']],
  ['The chokepoint', ['gateway', 'jobs', 'runs']],
  ['Measurement', ['ml', 'forecast', 'evals', 'analytics', 'ops', 'observability', 'reports']],
  ['Multimodal and outside data', ['media', 'vision', 'voice', 'websearch']],
]

/** Pull the mermaid blocks out before `marked` entity-escapes their arrows. */
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

/** Inter-document links become internal anchors; the book has no siblings. */
function rewriteLinks(html) {
  return html.replace(/href="([A-Za-z0-9._-]+)\.md(#[^"]*)?"/g, (_, stem) => `href="#mod-${stem}"`)
}

/** Demote every heading one level so module `h1`s sit under the part `h1`. */
function demote(html) {
  return html.replace(/<(\/?)h([1-5])([^>]*)>/g, (_, slash, n, rest) => `<${slash}h${Number(n) + 1}${rest}>`)
}

const CSS = `
@page { size: A4; margin: 18mm 16mm 20mm; }
:root{--ink:#101828;--muted:#667085;--line:#e4e7ec;--accent:#1570ef;--code:#f6f8fa}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);background:#fff;
  font:10.5pt/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
  -webkit-font-smoothing:antialiased}

/* Title page */
.title{height:245mm;display:flex;flex-direction:column;justify-content:center;
  page-break-after:always;break-after:page}
.title h1{font-size:34pt;line-height:1.1;letter-spacing:-.03em;margin:0 0 .35em}
.title .sub{font-size:13pt;color:var(--muted);max-width:34em;line-height:1.6}
.title .meta{margin-top:auto;font-size:9pt;color:var(--muted);border-top:1px solid var(--line);
  padding-top:10px}

/* Contents */
.toc{page-break-after:always;break-after:page}
.toc h2{font-size:16pt;letter-spacing:-.02em;margin:0 0 1em}
.toc .grp{font-size:8.5pt;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
  margin:1.4em 0 .5em}
.toc ol{list-style:none;margin:0;padding:0}
.toc li{display:flex;gap:.6em;padding:2.5px 0;font-size:10pt}
.toc li .n{color:var(--muted);font-variant-numeric:tabular-nums;min-width:1.7em}
.toc a{color:var(--ink);text-decoration:none}

/* Each module starts a page */
.mod{page-break-before:always;break-before:page}
.mod > h1{font-size:20pt;letter-spacing:-.025em;margin:0 0 .1em;padding-bottom:.25em;
  border-bottom:2px solid var(--ink)}
.mod .eyebrow{font-size:8pt;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);
  margin:0 0 .5em}

h2{font-size:12.5pt;letter-spacing:-.01em;margin:1.5em 0 .5em;color:var(--ink);
  page-break-after:avoid;break-after:avoid}
h3{font-size:11pt;margin:1.2em 0 .4em;page-break-after:avoid;break-after:avoid}
p{margin:0 0 .75em}
ul,ol{margin:0 0 .8em;padding-left:1.35em}
li{margin:.18em 0}
strong{font-weight:650}
a{color:var(--accent);text-decoration:none}

code{background:var(--code);border:1px solid var(--line);border-radius:3px;
  padding:.06em .3em;font-family:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:.87em}
pre{background:var(--code);border:1px solid var(--line);border-radius:5px;padding:9px 11px;
  overflow:hidden;page-break-inside:avoid;break-inside:avoid;margin:0 0 .9em}
pre code{background:none;border:0;padding:0;font-size:8.5pt;line-height:1.5;
  white-space:pre-wrap;word-break:break-word}

table{width:100%;border-collapse:collapse;margin:0 0 1em;font-size:9pt;
  page-break-inside:avoid;break-inside:avoid}
th,td{border:1px solid var(--line);padding:5px 7px;text-align:left;vertical-align:top}
th{background:#f9fafb;font-weight:650;font-size:8.5pt;text-transform:uppercase;
  letter-spacing:.04em;color:var(--muted)}
td code{font-size:8.2pt}

blockquote{margin:0 0 .9em;padding:.1em 0 .1em .9em;border-left:3px solid var(--line);
  color:var(--muted)}
hr{border:0;border-top:1px solid var(--line);margin:1.4em 0}

/* A tall flowchart renders far taller than a page. With break-inside:avoid a box
   that cannot fit anywhere is stranded and prints blank, so the height is capped
   and the viewBox scales the drawing down to fit rather than overflowing. */
pre.mermaid{background:#fff;border:1px solid var(--line);text-align:center;padding:10px;
  page-break-inside:avoid;break-inside:avoid;margin:0 0 1em}
pre.mermaid svg{max-width:100%!important;max-height:198mm!important;
  width:auto!important;height:auto!important}
`

async function main() {
  if (!existsSync(TEACHING)) {
    console.error(`No docs/teaching at ${TEACHING}`)
    process.exit(1)
  }

  const claimed = new Set(GROUPS.flatMap(([, stems]) => stems))
  const { readdir } = await import('node:fs/promises')
  const onDisk = (await readdir(TEACHING))
    .filter((n) => n.endsWith('.md') && n !== 'README.md' && !n.startsWith('persona-'))
    .map((n) => n.replace(/\.md$/, ''))
    .sort()

  // A module nobody placed still gets into the book, in the wrong place rather
  // than nowhere. Silent omission is the failure mode worth engineering against.
  const orphans = onDisk.filter((s) => !claimed.has(s))
  const groups = orphans.length > 0 ? [...GROUPS, ['Everything else', orphans]] : GROUPS

  let n = 0
  const toc = []
  const bodies = []

  for (const [group, stems] of groups) {
    const entries = []
    for (const stem of stems) {
      const file = join(TEACHING, `${stem}.md`)
      if (!existsSync(file)) {
        console.warn(`! ${stem}.md not found — skipped`)
        continue
      }
      n += 1
      const md = await readFile(file, 'utf8')
      // The chapter heading below carries the title, so the document's own H1 would
      // print it twice.
      const bodyMd = md.replace(/^#\s+.+$/m, '')
      const { stripped, blocks } = extractMermaid(bodyMd)
      let html = marked.parse(stripped, { mangle: false, headerIds: false })
      html = restoreMermaid(html, blocks)
      html = rewriteLinks(html)
      html = demote(html)

      const title = (md.match(/^#\s+(.+)$/m)?.[1] ?? stem).replace(/[*`_]/g, '')
      entries.push({ n, stem, title })
      bodies.push(
        `<section class="mod" id="mod-${stem}">` +
          `<p class="eyebrow">${group} &middot; ${String(n).padStart(2, '0')}</p>` +
          `<h1>${title}</h1>${html}</section>`,
      )
    }
    if (entries.length > 0) toc.push([group, entries])
  }

  const tocHtml = toc
    .map(
      ([group, entries]) =>
        `<p class="grp">${group}</p><ol>` +
        entries
          .map(
            (e) =>
              `<li><span class="n">${String(e.n).padStart(2, '0')}</span>` +
              `<a href="#mod-${e.stem}">${e.title}</a></li>`,
          )
          .join('') +
        `</ol>`,
    )
    .join('')

  const mermaidSrc = join(WEB_MODULES, 'mermaid', 'dist', 'mermaid.min.js')
  const hasMermaid = existsSync(mermaidSrc)
  if (!hasMermaid) console.warn('! mermaid.min.js missing — diagrams will print as code')
  const mermaidJs = hasMermaid ? await readFile(mermaidSrc, 'utf8') : ''

  const doc = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Aegis — the modules</title><style>${CSS}</style></head><body>
<div class="title">
  <h1>Aegis<br>module by module</h1>
  <p class="sub">${n} modules, one chapter each. What each one does, how it works,
  what it stores, what it enforces, and the routes it exposes.</p>
  <p class="meta">Generated from <code>docs/teaching/</code> &middot;
  every claim describes what is in the repository today</p>
</div>
<div class="toc"><h2>Contents</h2>${tocHtml}</div>
${bodies.join('\n')}
<script>${mermaidJs}</script>
<script>
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad:false, theme:'base', securityLevel:'loose',
      fontFamily:'Inter, -apple-system, sans-serif',
      themeVariables:{ background:'#ffffff', primaryColor:'#ffffff', primaryTextColor:'#101828',
        primaryBorderColor:'#e4e7ec', lineColor:'#98a2b3', secondaryColor:'#f9f9fa',
        tertiaryColor:'#f2f4f7', clusterBkg:'#f9f9fa', clusterBorder:'#e4e7ec', fontSize:'12px' },
      flowchart:{ curve:'basis', padding:10, useMaxWidth:true } })
    window.__diagrams = mermaid.run({ querySelector:'pre.mermaid' })
      .then(() => { window.__done = true })
      .catch((e) => { window.__done = true; window.__err = String(e) })
  } else { window.__done = true }
</script>
</body></html>`

  const scratch = join(TEACHING, '_assets')
  await mkdir(scratch, { recursive: true })
  const htmlPath = join(scratch, 'course-print.html')
  await writeFile(htmlPath, doc)

  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.goto(`file://${htmlPath}`, { waitUntil: 'load' })
  // Mermaid renders asynchronously; printing before it settles yields blank frames.
  await page.waitForFunction('window.__done === true', null, { timeout: 120000 })
  const err = await page.evaluate(() => window.__err ?? null)
  if (err) console.warn(`! mermaid reported: ${err}`)
  await page.emulateMedia({ media: 'print' })

  await page.pdf({
    path: OUT,
    format: 'A4',
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate:
      '<div style="width:100%;font-size:7.5pt;color:#98a2b3;padding:0 16mm;' +
      'font-family:-apple-system,sans-serif;display:flex;justify-content:space-between">' +
      '<span>Aegis &middot; module by module</span>' +
      '<span class="pageNumber"></span></div>',
    margin: { top: '18mm', bottom: '20mm', left: '16mm', right: '16mm' },
  })
  await browser.close()

  const { stat } = await import('node:fs/promises')
  const kb = Math.round((await stat(OUT)).size / 1024)
  console.log(`✓ ${OUT.replace(ROOT + '/', '')}  (${n} modules, ${kb} kB)`)
}

await main()
