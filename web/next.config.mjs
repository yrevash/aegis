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

  /**
   * Proxy the API onto this origin in development.
   *
   * Without this the console is only reachable when someone remembers to set
   * `NEXT_PUBLIC_API_BASE`, and every arrangement that forgets shows "Backend
   * unavailable" on every screen — which reads as a dead backend and is really a
   * missing base URL. It bit two lanes and the shared dev server.
   *
   * A rewrite is the right fix rather than `.env.local`, because pointing the
   * browser straight at `http://localhost:8110` breaks in two further ways: the
   * backend's CORS allowlist admits only a couple of origins, and it binds IPv4
   * only, so Chrome resolving `localhost` to `::1` gets connection-refused. Going
   * through this origin makes the API same-origin, so CORS never applies and a
   * tunnelled host works unchanged.
   *
   * Setting `NEXT_PUBLIC_API_BASE` still wins — these rewrites simply stop
   * matching, because the client then builds absolute URLs.
   */
  async rewrites() {
    const api = process.env.AEGIS_DEV_API_ORIGIN || 'http://127.0.0.1:8110'
    return [
      { source: '/v1/:path*', destination: `${api}/v1/:path*` },
      { source: '/health', destination: `${api}/health` },
      { source: '/ready', destination: `${api}/ready` },
      { source: '/readyz', destination: `${api}/readyz` },
    ]
  },
}

export default nextConfig
