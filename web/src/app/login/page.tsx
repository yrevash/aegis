'use client'

import { useRouter } from 'next/navigation'
import { LogIn, ShieldHalf, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import { probeBackend } from '@/lib/api/mode'
import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor, type Role } from '@/lib/portal'

/**
 * Real sign-in surface (port of `frontend/src/routes/LoginPage.tsx`). The role
 * returned by `signIn` decides which portal the user lands in (RBAC). In mock
 * mode the role is derived from the username, so the four demo quick-in buttons
 * each drop straight into their portal offline. Styled with the web tokens +
 * TailAdmin card, matching the console look.
 */

/** Quick-in demo identities → the username the mock login maps to each role. */
const QUICK_IN: { role: Role; label: string; username: string }[] = [
  { role: 'admin', label: 'Enter as Admin', username: 'admin' },
  { role: 'ai_team', label: 'Enter as AI team', username: 'ai' },
  { role: 'devops', label: 'Enter as DevOps', username: 'devops' },
  { role: 'client', label: 'Enter as Client', username: 'client' },
]

export default function LoginPage() {
  const { signIn, session, hydrated } = useAuth()
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Resolve backend mode on mount (live-first, mock fallback) so a sign-in reads
  // the correct transport — offline, the probe fails and the demo quick-in signs
  // in against the in-browser mock instead of a real POST that would fail.
  useEffect(() => {
    void probeBackend()
  }, [])

  // Already signed in → go to the role's home (mirrors the Vite <Navigate/>).
  useEffect(() => {
    if (hydrated && session) router.replace(homePathFor(session.role))
  }, [hydrated, session, router])

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await probeBackend()
      const s = await signIn(username || 'analyst', password)
      router.replace(homePathFor(s.role))
    } catch {
      setError('Sign-in failed. Check your credentials and try again.')
      setBusy(false)
    }
  }

  const quickIn = (name: string): void => {
    setUsername(name)
    setBusy(true)
    setError(null)
    void probeBackend()
      .then(() => signIn(name, 'demo'))
      .then((s) => router.replace(homePathFor(s.role)))
      .catch(() => {
        setError('Sign-in failed. Check your credentials and try again.')
        setBusy(false)
      })
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      {/* Left: identity / thesis */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-surface p-10 lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <ShieldHalf className="size-5" />
          </span>
          <span className="text-[0.95rem] font-semibold tracking-tight text-foreground">Aegis</span>
        </div>
        <div className="max-w-md">
          <p className="eyebrow mb-4">Bounded-autonomy AI, made watchable</p>
          <h2 className="text-3xl font-semibold leading-tight tracking-tight text-foreground">
            Every autonomous action is uncertainty-bounded, explainable, guarded,
            human-approved, and fully traced.
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            A control room for an agent that takes real actions — with the trust
            stack visible in real time.
          </p>
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <ShieldCheck className="size-4 text-primary" />
          <span className="font-mono text-[0.72rem] tracking-wide">
            RBAC · guardrails · SHAP · conformal · OTel audit
          </span>
        </div>
      </aside>

      {/* Right: form */}
      <main className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <ShieldHalf className="size-5" />
            </span>
            <span className="text-[0.95rem] font-semibold tracking-tight text-foreground">Aegis</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Sign in</h1>
          <p className="mb-6 mt-1 text-sm text-muted-foreground">
            Access is role-scoped: Admin, AI team, DevOps and Client each land in
            their own portal.
          </p>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="username" className="text-sm font-medium text-foreground">
                Username
              </label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="analyst"
                autoComplete="username"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="password" className="text-sm font-medium text-foreground">
                Password
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>
              <LogIn className="size-4" /> Sign in
            </Button>
          </form>

          <div className="mt-6 rounded-xl border border-border bg-surface p-3">
            <p className="eyebrow mb-2">Demo access</p>
            <div className="grid grid-cols-2 gap-2">
              {QUICK_IN.map((q) => (
                <Button
                  key={q.role}
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => quickIn(q.username)}
                >
                  {q.label}
                </Button>
              ))}
            </div>
            <p className="mt-2 text-[0.72rem] leading-snug text-muted-foreground">
              Signs in with a demo identity for that role — the whole console runs
              offline on mock data.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
