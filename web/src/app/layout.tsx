import type { Metadata } from 'next'
import './globals.css'
import { Providers } from '@/components/auth/Providers'

export const metadata: Metadata = {
  title: 'Aegis Console',
  description:
    'Aegis — domain-agnostic enterprise agentic-AI platform. Role-scoped portals for admin, AI team, DevOps and client.',
}

/**
 * Root layout. Fonts (Inter / Space Grotesk / JetBrains Mono) are loaded via a
 * plain <link> at runtime rather than next/font so `next build` never depends on
 * a network fetch; the CSS font stacks in globals.css name these families first.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
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
