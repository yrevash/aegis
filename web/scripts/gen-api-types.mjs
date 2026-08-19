/**
 * Generate `src/lib/api/generated/schema.d.ts` from the backend's OpenAPI document.
 *
 * §8.7. Before this, 696 hand-written lines in `src/lib/api/types.ts` mirrored 1,598
 * lines of Pydantic, and the drift was not hypothetical: `schemas.py` and `types.ts`
 * have been edited together in a single change more than once, by hand, each time
 * relying on somebody remembering both sides.
 *
 * What is generated is **types only**. The runtime layer — `client.ts`'s `request()`
 * with its one-shot 401 sign-out, `apiError.ts`, `authToken.ts`, and the hand-rolled
 * SSE reader in `sse.ts` — is kept and composes on top: it now speaks the generated
 * types instead of hand-written mirrors of them. A full client generator would have
 * replaced exactly the code that carries this console's hard-won behaviour, and the
 * phase plan names that risk out loud ("a generated client nobody wants to use is not
 * an improvement").
 *
 * Usage:
 *
 *   npm run gen:api           # write the file
 *   npm run gen:api:check     # exit 1 if regenerating would change it
 *
 * The input, `backend/openapi.json`, is itself committed and snapshot-tested
 * (`backend/tests/api/test_openapi_snapshot.py`), so the chain from a Pydantic field to
 * a TypeScript property has a failing test at every link.
 */

import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'

const HERE = dirname(fileURLToPath(import.meta.url))

/** The committed OpenAPI document: `backend/.venv/bin/python scripts/build_openapi.py`. */
export const OPENAPI = resolve(HERE, '../../backend/openapi.json')

/** The generated output. Committed, because `next build` depends on it. */
export const OUTPUT = resolve(HERE, '../src/lib/api/generated/schema.d.ts')

const BANNER = `/**
 * GENERATED FILE — DO NOT EDIT.
 *
 * Every type here is derived from \`backend/openapi.json\`, which is derived from the
 * FastAPI route table and the Pydantic models behind it. To change anything in this
 * file, change the Python and regenerate:
 *
 *   backend/.venv/bin/python scripts/build_openapi.py
 *   cd web && npm run gen:api
 *
 * \`web/tests/api/generatedSchema.test.mjs\` fails if this file and that document
 * disagree, so an edit made here by hand does not survive CI.
 */

`

/** Render the generated module exactly as it should appear on disk. */
export async function render() {
  const document = JSON.parse(await readFile(OPENAPI, 'utf8'))
  const ast = await openapiTS(document, { alphabetize: false })
  return BANNER + astToString(ast)
}

/** Write the file, or check it. Exported so the test drives the same code path. */
export async function main(argv = process.argv.slice(2)) {
  const contents = await render()
  if (argv.includes('--check')) {
    const current = existsSync(OUTPUT) ? await readFile(OUTPUT, 'utf8') : ''
    if (current !== contents) {
      console.error(`${OUTPUT} is STALE.\nRegenerate it with:  cd web && npm run gen:api`)
      return 1
    }
    console.log(`${OUTPUT} is current.`)
    return 0
  }
  await mkdir(dirname(OUTPUT), { recursive: true })
  await writeFile(OUTPUT, contents, 'utf8')
  console.log(`wrote ${OUTPUT} (${contents.split('\n').length} lines)`)
  return 0
}

// Run only when invoked as a script — `render` is imported by the test, and a module
// that regenerated the file on import would make that test unable to fail.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.exit(await main())
}
