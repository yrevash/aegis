import type { Metadata } from 'next'
import './globals.css'
import { Providers } from '@/components/auth/Providers'
import { TEXT_SCALE_BOOT } from '@/components/settings/textScale'

export const metadata: Metadata = {
  title: 'Aegis Console',
  description:
    'Aegis — domain-agnostic enterprise agentic-AI platform. Role-scoped portals for admin, AI team, DevOps and client.',
}

/**
 * Root layout. Fonts (Inter / Space Grotesk / JetBrains Mono) are loaded via a
 * plain <link> at runtime rather than next/font so `next build` never depends on
 * a network fetch; the CSS font stacks in globals.css name these families first.
 *
 * The one inline script is the reader's text-size step, applied to `<html>` before the
 * first paint. It has to be here, synchronous and in the head: an effect runs after the
 * page has painted, so someone reading at 125% would watch every screen load at 100%
 * and jump. It reads one integer from `localStorage`, ignores anything that is not one
 * of the four declared steps, and does nothing at all if storage throws — see
 * `components/settings/textScale.ts`, which owns the string and is tested against it.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning` is on `<html>` for exactly one reason, and it is scoped
    // to this element's own attributes rather than to the tree: the text-size script below
    // writes `style="font-size:…"` onto `<html>` before React hydrates, which is by
    // definition a difference between the server's markup and the client's DOM. React
    // reports it as a mismatch it "won't patch up" — the size does keep working, but a
    // warning nobody can act on is a warning everybody learns to scroll past. Children are
    // unaffected: the flag does not descend past this element's own attributes.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: TEXT_SCALE_BOOT }} />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* Loaded at runtime (not next/font) so `next build` never blocks on a
            font fetch; the CSS stacks in globals.css name these families first. */}
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
