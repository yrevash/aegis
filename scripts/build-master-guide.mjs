#!/usr/bin/env node
/**
 * Bind `docs/guide/` into the Aegis master guide — one printable book.
 *
 * ## Why this is separate from `build-teaching-pdf.mjs`
 *
 * That script binds the 34-module reference course: one chapter per module, in the
 * curriculum's own order, for a reader who wants to look something up. This binds a
 * different book with a different job — a single narrative a beginner reads front to
 * back, ending able to explain the platform and defend it under questioning.
 *
 * The two could have shared a builder behind a flag. They do not, because the print
 * decisions genuinely differ: this one runs tighter leading, keeps every part starting
 * on a right-hand page, and prints the cross-question blocks as boxed callouts, which
 * the reference course has no concept of.
 *
 * ## Why Playwright rather than a Markdown-to-PDF converter
 *
 * The diagrams. Mermaid renders in a browser — it turns source into SVG at run time. A
 * converter that never executes JavaScript emits those blocks as code listings, which
 * throws away the most useful thing on the page. So: Markdown to one HTML document,
 * open it in a real browser, wait for mermaid to settle, then print.
 *
 * Mermaid is loaded from `web/node_modules` and inlined, so the render needs no network
 * and cannot silently produce a book full of blank frames on an offline machine.
 *
 * Usage:  node scripts/build-master-guide.mjs
 * Output: docs/guide/Aegis-master-guide.pdf
 */

import { createRequire } from 'node:module'
import { existsSync } from 'node:fs'
import { readFile, writeFile, mkdir, readdir, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const GUIDE = join(ROOT, 'docs', 'guide')
const WEB_MODULES = join(ROOT, 'web', 'node_modules')
const OUT = join(GUIDE, 'Aegis-master-guide.pdf')

const require = createRequire(join(WEB_MODULES, 'noop.js'))
let marked, chromium
try {
  ;({ marked } = require('marked'))
  ;({ chromium } = require('playwright'))
} catch {
  console.error(
    'Needs `marked` and `playwright` from web/node_modules.\n' +
      'Run `npm install` in web/ first — this script reuses the console\'s deps.',
  )
  process.exit(1)
}

const CSS = `
  @page { size: A4; }
  * { box-sizing: border-box; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body {
    margin: 0; color: #101828; background: #fff;
    font: 9.8pt/1.42 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  }

  /* ── Title page ─────────────────────────────────────────────────────── */
  .title { height: 250mm; display: flex; flex-direction: column; justify-content: center;
           page-break-after: always; }
  .title h1 { font-size: 40pt; line-height: 1.05; letter-spacing: -0.02em; margin: 0 0 10mm;
              font-weight: 600; }
  .title .sub { font-size: 13pt; line-height: 1.5; color: #475467; max-width: 118mm; margin: 0 0 16mm; }
  .title .meta { font-size: 9pt; color: #98a2b3; margin: 0; }
  .rule { height: 3px; width: 54mm; background: #1570ef; margin: 0 0 9mm; }

  /* ── Contents ───────────────────────────────────────────────────────── */
  .toc { page-break-after: always; }
  .toc h2 { font-size: 17pt; margin: 0 0 7mm; font-weight: 600; }
  .toc ol { list-style: none; padding: 0; margin: 0 0 6mm; }
  .toc li { display: flex; gap: 5mm; padding: 1.6mm 0; border-bottom: 1px solid #f2f4f7;
            font-size: 10.5pt; }
  .toc .n { color: #98a2b3; font-variant-numeric: tabular-nums; min-width: 8mm; }
  .toc a { color: #101828; text-decoration: none; }

  /* ── Parts ──────────────────────────────────────────────────────────── */
  .part { page-break-before: always; }
  h1 { font-size: 19pt; line-height: 1.18; letter-spacing: -0.01em; margin: 0 0 4.5mm;
       font-weight: 600; padding-bottom: 3mm; border-bottom: 2px solid #1570ef; }
  h2 { font-size: 12.5pt; margin: 6mm 0 2.4mm; font-weight: 600; page-break-after: avoid; }
  h3 { font-size: 10.5pt; margin: 4.2mm 0 1.6mm; font-weight: 600; color: #344054;
       page-break-after: avoid; }
  h4 { font-size: 9.8pt; margin: 3mm 0 1.2mm; font-weight: 600; color: #475467; }
  p { margin: 0 0 2.3mm; }
  ul, ol { margin: 0 0 3mm; padding-left: 5.5mm; }
  li { margin: 0 0 0.8mm; }
  strong { font-weight: 600; }

  code { font: 9pt/1.4 'JetBrains Mono', ui-monospace, monospace;
         background: #f6f8fa; padding: 0.4mm 1.1mm; border-radius: 2px; }
  pre { background: #f6f8fa; border: 1px solid #e4e7ec; border-radius: 4px;
        padding: 2.2mm 3mm; overflow: hidden; page-break-inside: avoid; margin: 0 0 3mm; }
  pre code { background: none; padding: 0; font-size: 8.5pt; line-height: 1.45; }

  table { width: 100%; border-collapse: collapse; margin: 0 0 3mm; font-size: 8.8pt;
          page-break-inside: avoid; }
  th { text-align: left; font-weight: 600; border-bottom: 1.5px solid #d0d5dd;
       padding: 1.3mm 2.5mm 1.3mm 0; }
  td { border-bottom: 1px solid #f2f4f7; padding: 1.3mm 2.5mm 1.3mm 0; vertical-align: top; }
  td:last-child, th:last-child { padding-right: 0; }

  blockquote { margin: 0 0 4mm; padding: 2.5mm 0 2.5mm 4mm; border-left: 3px solid #1570ef;
               color: #344054; }
  blockquote p:last-child { margin-bottom: 0; }

  /* Cross-question blocks: the examiner's page, boxed so it reads as a unit. */
  h3.xq { background: #eff8ff; border-left: 3px solid #1570ef; border-radius: 3px;
    padding: 1.8mm 3mm; margin-top: 5mm; color: #101828; font-size: 10pt; }

  pre.mermaid { background: #fff; border: 1px solid #e4e7ec; text-align: center;
                padding: 4mm 2mm; page-break-inside: avoid; }
  pre.mermaid svg { max-width: 100%; height: auto; }

  hr { border: 0; border-top: 1px solid #e4e7ec; margin: 6mm 0; }
`

/** Turn ```mermaid fences into the <pre class="mermaid"> the renderer looks for. */
function renderer() {
  const r = new marked.Renderer()
  const base = r.code.bind(r)
  r.code = (code, lang, escaped) => {
    const text = typeof code === 'object' && code !== null ? code.text : code
    const language = typeof code === 'object' && code !== null ? code.lang : lang
    if (language === 'mermaid') {
      const safe = String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
      return `<pre class="mermaid">${safe}</pre>`
    }
    return base(code, lang, escaped)
  }

  // Mark the examiner blocks so the stylesheet can box them.
  //
  // The obvious selector — `h3[id^="cross-questions"]` — matches nothing: `marked`
  // stopped emitting heading ids by default, so the rule silently applied to no
  // element and the blocks printed as ordinary subheadings. Tagging them here is not
  // dependent on that behaviour returning.
  const baseHeading = r.heading.bind(r)
  r.heading = (...args) => {
    const html = baseHeading(...args)
    return /^<h3[^>]*>\s*Cross-questions\s*<\/h3>/i.test(html)
      ? html.replace('<h3', '<h3 class="xq"')
      : html
  }
  return r
}

async function main() {
  if (!existsSync(GUIDE)) {
    console.error(`No docs/guide at ${GUIDE}`)
    process.exit(1)
  }

  // Numeric order from the filename. Alphabetical would be a lie the moment a part
  // reaches double digits, and the parts are a narrative — order is the content.
  const files = (await readdir(GUIDE))
    .filter((f) => /^\d\d-.*\.md$/.test(f))
    .sort()

  if (files.length === 0) {
    console.error('No `NN-name.md` parts in docs/guide/')
    process.exit(1)
  }

  const parts = []
  for (const f of files) {
    const md = await readFile(join(GUIDE, f), 'utf8')
    const first = md.split('\n').find((l) => l.startsWith('# '))
    const title = (first ?? f).replace(/^#\s*/, '').trim()
    const id = f.replace(/\.md$/, '')
    const html = marked.parse(md, { renderer: renderer(), mangle: false, headerIds: true })
    parts.push({ id, title, html })
  }

  const toc =
    `<ol>` +
    parts
      .map(
        (p, i) =>
          `<li><span class="n">${String(i + 1).padStart(2, '0')}</span>` +
          `<a href="#${p.id}">${p.title.replace(/^Part \d+\s*[—-]\s*/, '')}</a></li>`,
      )
      .join('') +
    `</ol>`

  const bodies = parts
    .map((p) => `<section class="part" id="${p.id}">${p.html}</section>`)
    .join('\n')

  const mermaidSrc = join(WEB_MODULES, 'mermaid', 'dist', 'mermaid.min.js')
  const hasMermaid = existsSync(mermaidSrc)
  if (!hasMermaid) console.warn('! mermaid.min.js missing — diagrams will print as code')
  const mermaidJs = hasMermaid ? await readFile(mermaidSrc, 'utf8') : ''

  const doc = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Aegis — the master guide</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=JetBrains+Mono:wght@400&display=swap">
<style>${CSS}</style></head><body>
<div class="title">
  <div class="rule"></div>
  <h1>Aegis<br>the master guide</h1>
  <p class="sub">What the platform is, how every part of it works, why each choice was
  made over the alternatives — and the questions you should be able to answer when
  somebody pushes back.</p>
  <p class="meta">Written to be read front to back &middot; every claim describes what is
  in the repository today</p>
</div>
<div class="toc"><h2>Contents</h2>${toc}</div>
${bodies}
<script>${mermaidJs}</script>
<script>
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad:false, theme:'base', securityLevel:'loose',
      fontFamily:'IBM Plex Sans, -apple-system, sans-serif',
      themeVariables:{ background:'#ffffff', primaryColor:'#ffffff', primaryTextColor:'#101828',
        primaryBorderColor:'#d0d5dd', lineColor:'#98a2b3', secondaryColor:'#f9f9fa',
        tertiaryColor:'#f2f4f7', clusterBkg:'#f9f9fa', clusterBorder:'#e4e7ec', fontSize:'11px' },
      flowchart:{ curve:'basis', padding:8, useMaxWidth:true } })
    window.__diagrams = mermaid.run({ querySelector:'pre.mermaid' })
      .then(() => { window.__done = true })
      .catch((e) => { window.__done = true; window.__err = String(e) })
  } else { window.__done = true }
</script>
</body></html>`

  const scratch = join(GUIDE, '_assets')
  await mkdir(scratch, { recursive: true })
  const htmlPath = join(scratch, 'guide-print.html')
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
      '<span>Aegis &middot; the master guide</span>' +
      '<span class="pageNumber"></span></div>',
    margin: { top: '13mm', bottom: '14mm', left: '15mm', right: '15mm' },
  })
  await browser.close()

  const { size } = await stat(OUT)
  console.log(
    `✓ ${parts.length} parts  ${(size / 1024 / 1024).toFixed(1)} MB\n  ${OUT}`,
  )
}

await main()
