/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  /**
   * The dev proxy's own 30-second ceiling was failing the demo.
   *
   * Next's rewrite proxy defaults to `proxyTimeout: 30_000`, and it applies to the
   * rewrites below. `POST /v1/evals/live-run` is LLM-judged: measured backend-side
   * durations for the same route span **14 s → 32 s → 134 s** depending on provider
   * latency and load. Measured through this origin against a cold backend, two
   * consecutive presses returned **HTTP 500 at t=30.007s and t=30.016s**, while the
   * same request warm returned 200 at t=15s.
   *
   * So the button was a coin-flip on the first press after a restart — exactly the demo
   * scenario — and the failure mode is the bad one: the proxy gives up, the browser
   * shows "could not run", and **the backend keeps running and keeps spending**.
   *
   * 180 s clears the worst measured case with room. This is a dev-server concern only;
   * production serves the API from its own origin and never passes through here.
   */
  experimental: {
    proxyTimeout: 180_000,
  },

  /**
   * No gzip from the Next server — because it was eating the whole event stream.
   *
   * Measured, not guessed. The same `POST /v1/query` through this origin:
   *
   * ```
   * curl -N                        → 40 frames, first at +0.0s, guardrail at +10.7s
   * curl -N -H 'Accept-Encoding: gzip' → 1 frame, 2,260 bytes, at +72.5s
   * ```
   *
   * The backend is innocent: hit `127.0.0.1:8110` directly with the same header and
   * it answers `transfer-encoding: chunked` with no `Content-Encoding` at all, and
   * every frame arrives when it happens. It is Next's own `compress: true` default
   * that gzips the proxied response, and its compressor holds the stream until the
   * upstream closes — so a browser (which always sends `Accept-Encoding: gzip`) got
   * one burst of 40 events after the run had already finished.
   *
   * That is the whole of "after search no streaming". Not the console's rendering,
   * not the `stream` node's 0 ms: every live surface in this product — the chat
   * console, RAG, Harness, Graph, Voice, Simulation — watched a run in silence and
   * then had its entire history handed to it at once, and the answer landed as one
   * paste because all 64 `token` events did.
   *
   * `compress` governs only what this Node server does to its own responses. In
   * production the CDN/edge negotiates encoding, and it knows not to buffer
   * `text/event-stream`; there is no per-route escape hatch here, and there is no
   * version of this product where holding an SSE stream for 72 seconds is the right
   * trade against gzipping a few HTML documents in dev.
   */
  compress: false,

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
