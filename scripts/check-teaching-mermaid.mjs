/**
 * Parse every ```mermaid block in docs/teaching/ and report the ones Mermaid rejects.
 *
 * A broken diagram does not fail the HTML build — it renders as an error box in the
 * page, which is exactly the kind of thing nobody notices until a reader clicks it.
 * So this is a separate gate: run it before `build-teaching-html.mjs`.
 *
 * Mermaid needs a DOM, so this drives the real bundle inside headless Chrome over the
 * DevTools protocol rather than pretending jsdom is close enough.
 *
 * Usage: node scripts/check-teaching-mermaid.mjs        (exit 1 if any block fails)
 */
import { readdir, readFile, mkdtemp, rm } from 'node:fs/promises'
import { join, dirname, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const TEACHING = join(ROOT, 'docs', 'teaching')
const MERMAID = join(ROOT, 'web', 'node_modules', 'mermaid', 'dist', 'mermaid.min.js')
const CHROME =
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9339

if (!existsSync(MERMAID)) {
  console.error(`mermaid not found at ${MERMAID}\nRun \`npm install\` in web/ first.`)
  process.exit(1)
}

/* ── collect every fenced mermaid block, with its source line ─────────────── */

async function walk(dir) {
  const out = []
  for (const e of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, e.name)
    if (e.isDirectory()) out.push(...(await walk(full)))
    else if (e.name.endsWith('.md')) out.push(full)
  }
  return out
}

const blocks = []
for (const file of (await walk(TEACHING)).sort()) {
  const lines = (await readFile(file, 'utf8')).split('\n')
  let start = -1
  let buf = []
  for (const [i, line] of lines.entries()) {
    if (start < 0 && line.trim() === '```mermaid') {
      start = i + 1
      buf = []
    } else if (start >= 0 && line.trim() === '```') {
      blocks.push({ file: relative(ROOT, file), line: start, code: buf.join('\n') })
      start = -1
    } else if (start >= 0) {
      buf.push(line)
    }
  }
}

if (!blocks.length) {
  console.log('No mermaid blocks found.')
  process.exit(0)
}

/* ── parse them all inside a real browser ────────────────────────────────── */

// The scratch profile goes to the OS temp dir, not the repo — a checker that leaves an
// untracked directory behind turns every `git status` after it into a false positive.
const profile = await mkdtemp(join(tmpdir(), 'aegis-mermaid-'))

const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    'about:blank',
  ],
  { stdio: 'ignore' },
)

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function wsUrl() {
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const tabs = await r.json()
      const page = tabs.find((t) => t.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* not up yet */
    }
    await sleep(250)
  }
  throw new Error('Chrome did not expose a debugging target')
}

let failed = 0
try {
  const { default: WS } = await import('node:worker_threads').then(() => ({ default: null }))
  void WS
  const url = await wsUrl()
  const ws = new WebSocket(url)
  await new Promise((res, rej) => {
    ws.onopen = res
    ws.onerror = rej
  })

  let id = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (pending.has(msg.id)) {
      pending.get(msg.id)(msg)
      pending.delete(msg.id)
    }
  }
  const send = (method, params) =>
    new Promise((res) => {
      const mid = ++id
      pending.set(mid, res)
      ws.send(JSON.stringify({ id: mid, method, params }))
    })

  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (r.result?.exceptionDetails) {
      throw new Error(r.result.exceptionDetails.text)
    }
    return r.result?.result?.value
  }

  await send('Runtime.enable')
  await evaluate(`document.body.innerHTML = '<div id="sink"></div>'`)
  await evaluate(await readFile(MERMAID, 'utf8'))
  await evaluate(`mermaid.initialize({ startOnLoad: false })`)

  for (const b of blocks) {
    const payload = JSON.stringify(b.code)
    const err = await evaluate(
      `(async () => {
         try { await mermaid.parse(${payload}); return null }
         catch (e) { return String(e && e.message ? e.message : e) }
       })()`,
    )
    if (err) {
      failed++
      console.log(`\x1b[31mFAIL\x1b[0m ${b.file}:${b.line}`)
      console.log(
        '     ' + err.split('\n').slice(0, 4).join('\n     ').slice(0, 400) + '\n',
      )
    }
  }
  ws.close()
} finally {
  chrome.kill()
  await rm(profile, { recursive: true, force: true })
}

const ok = blocks.length - failed
console.log(`\n${ok}/${blocks.length} mermaid blocks parse.`)
process.exit(failed ? 1 : 0)
