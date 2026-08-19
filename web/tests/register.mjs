/** Install the TypeScript-source resolver before the test files load. */
import { register } from 'node:module'

register('./tsResolve.mjs', import.meta.url)
