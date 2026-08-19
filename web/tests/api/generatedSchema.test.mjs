/**
 * The generated client is reproducible, and it points at `/v1`.
 *
 * Two properties, both of which have failed silently in this project before:
 *
 * 1. **Regenerating produces no diff.** `src/lib/api/generated/schema.d.ts` is committed
 *    because `next build` depends on it — unlike `docs/api/`, which is git-ignored — and
 *    a committed generated file is only trustworthy if a machine checks it is current.
 *    Without this test, "generated" would degrade to "generated once, then hand-edited",
 *    which is where the 696-line hand-written mirror it replaced came from.
 *
 * 2. **Every product call carries the version segment, and the boot probe does not.**
 *    The prefix moved in one place (`config.ts`), which is only safe if something
 *    asserts that the one place is really the only place — 60 call sites write
 *    `${API_BASE}${path}` and one of them (`health.ts`) deliberately does not.
 *
 * Nothing here reaches a network: the generator reads a committed JSON file, and the
 * client is driven against a stubbed `fetch`.
 */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test, { afterEach, beforeEach } from 'node:test'

import { OUTPUT, render } from '../../scripts/gen-api-types.mjs'
import { API_BASE, API_ORIGIN, API_VERSION } from '../../src/lib/api/config.ts'
import { getGraph } from '../../src/lib/api/client.ts'
import { probeBackend } from '../../src/lib/api/health.ts'

const realFetch = globalThis.fetch
const realConsoleError = console.error

/** A `fetch` that records the URLs it is asked for and answers `{}`. */
function recording() {
  const urls = []
  globalThis.fetch = async (url) => {
    urls.push(String(url))
    return { ok: true, status: 200, statusText: 'Stubbed', json: async () => ({}) }
  }
  return urls
}

beforeEach(() => {
  console.error = () => {}
})

afterEach(() => {
  globalThis.fetch = realFetch
  console.error = realConsoleError
})

test('regenerating the client produces no diff', async () => {
  const committed = await readFile(OUTPUT, 'utf8')
  const regenerated = await render()
  assert.equal(
    regenerated,
    committed,
    `${OUTPUT} is not what the generator produces from backend/openapi.json.\n` +
      'Either it was edited by hand, or the OpenAPI document moved without it.\n' +
      'Fix:  cd web && npm run gen:api',
  )
})

test('a product call is versioned; the boot probe is not', async () => {
  assert.equal(API_BASE, `${API_ORIGIN}/${API_VERSION}`)

  const urls = recording()
  await getGraph(null)
  assert.deepEqual(urls, [`${API_BASE}/graph`])
  assert.ok(urls[0].startsWith(`${API_ORIGIN}/v1/`), `not versioned: ${urls[0]}`)

  urls.length = 0
  await probeBackend()
  assert.deepEqual(urls, [`${API_ORIGIN}/health`])
  assert.ok(
    !urls[0].includes('/v1'),
    'the liveness probe moved under /v1 — a load balancer configured with /health ' +
      'would 404 through the whole rollout',
  )
})
