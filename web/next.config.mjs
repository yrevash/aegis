/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  /**
   * Build output directory, overridable per process.
   *
   * `next build` writes the same `.next` that a running `next dev` serves from, so
   * a build kicked off while dev servers are up deletes the chunks they are still
   * handing to browsers. With several agents working the same checkout that showed
   * up as `Cannot find module './vendor-chunks/recharts.js'` and 404s on page
   * chunks — failures that look like a broken import and are really two processes
   * sharing one directory. Three separate lanes lost time to it and each worked
   * around it by copying the whole tree.
   *
   * Set `AEGIS_DIST_DIR` to give a build its own output:
   *   AEGIS_DIST_DIR=.next-verify npx next build
   */
  distDir: process.env.AEGIS_DIST_DIR || '.next',
}

export default nextConfig
