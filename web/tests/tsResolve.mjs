/**
 * Resolve the project's own imports to their TypeScript source.
 *
 * The console's pure modules are plain `.ts` with type-only imports, so Node runs them
 * directly under its built-in type stripping — no bundler, no test framework, no new
 * dependency. Two things Node's ESM resolver will not do, and the bundler will:
 *
 * 1. **Guess an extension.** The project's imports are written extensionless
 *    (`./eventViews`) because that is what the bundler expects.
 * 2. **Read `tsconfig.json`'s `paths`.** `@/*` maps to `src/*`, and a module that
 *    imports a *value* across that alias — `threadReducer` importing `runReducer` — is
 *    unresolvable without it. Type-only `@/` imports were invisible here because type
 *    stripping erases them, which is why this gap survived until a reducer was tested.
 *
 * This hook closes exactly those two gaps and nothing else.
 */

import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const EXTENSIONS = ['.ts', '.tsx', '.mjs', '.js']
const HAS_EXTENSION = /\.[cm]?[jt]sx?$/

/** `@/*` → `web/src/*`, the one alias `tsconfig.json` declares. */
const ALIAS = '@/'
const SRC = new URL('../src/', import.meta.url)

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith(ALIAS)) {
    return nextResolve(withExtension(new URL(specifier.slice(ALIAS.length), SRC)), context)
  }
  if (specifier.startsWith('./') || specifier.startsWith('../')) {
    return nextResolve(withExtension(new URL(specifier, context.parentURL)), context)
  }
  return nextResolve(specifier, context)
}

/** The first extension that exists on disk, or the URL unchanged. */
function withExtension(url) {
  if (HAS_EXTENSION.test(url.pathname)) return url.href
  for (const extension of EXTENSIONS) {
    const candidate = new URL(url.href + extension)
    if (existsSync(fileURLToPath(candidate))) return candidate.href
  }
  return url.href
}
