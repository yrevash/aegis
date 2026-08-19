/**
 * Resolve extensionless relative imports to their TypeScript source.
 *
 * The console's pure modules are plain `.ts` with type-only imports, so Node runs them
 * directly under its built-in type stripping — no bundler, no test framework, no new
 * dependency. The one thing Node's ESM resolver will not do is guess an extension, and
 * the project's own imports are written extensionless (`./eventViews`) because that is
 * what the bundler expects. This hook closes exactly that gap and nothing else.
 */

import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const EXTENSIONS = ['.ts', '.tsx', '.mjs', '.js']
const HAS_EXTENSION = /\.[cm]?[jt]sx?$/

export async function resolve(specifier, context, nextResolve) {
  const relative = specifier.startsWith('./') || specifier.startsWith('../')
  if (relative && !HAS_EXTENSION.test(specifier)) {
    for (const extension of EXTENSIONS) {
      const candidate = new URL(specifier + extension, context.parentURL)
      if (existsSync(fileURLToPath(candidate))) return nextResolve(candidate.href, context)
    }
  }
  return nextResolve(specifier, context)
}
