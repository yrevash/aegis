'use client'

import { useRouter } from 'next/navigation'
import { LogIn, ShieldCheck } from 'lucide-react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import { LoginError } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor } from '@/lib/portal'

/**
 * Real sign-in surface (port of `frontend/src/routes/LoginPage.tsx`). The role
 * returned by `signIn` decides which portal the user lands in (RBAC) — the
 * backend is the only authority on it. Styled with the web tokens + TailAdmin
 * card, matching the console look.
 */

/**
 * Quick-in identities. Each is a real `users` row written by `python -m app.seed`
 * (`backend/src/app/seed.py`, password `demo` unless `AEGIS_SEED_PASSWORD` says
 * otherwise), so these buttons issue an ordinary `POST /auth/login` like the form does.
 * An unseeded backend answers 503 and says to run the seed — there is no fallback
 * login table behind these names.
 *
 * Five, not four: `northwind.admin` is the seed's tenant administrator, and until
 * §7.2 there was no way to reach that portal from this screen at all — the un-tenanted
 * `admin` account is the *platform* operator, and the two are different jobs with
 * different screens. Which portal each lands in is decided by the backend's
 * `fine_role`, never by this list.
 */
const QUICK_IN: { label: string; username: string }[] = [
  { label: 'Enter as Platform admin', username: 'admin' },
  { label: 'Enter as Tenant admin', username: 'northwind.admin' },
  { label: 'Enter as AI team', username: 'ai' },
  { label: 'Enter as DevOps', username: 'devops' },
  { label: 'Enter as Client', username: 'client' },
]

/**
 * Turn a failed sign-in into the message the operator needs.
 *
 * A 401 really is "check your credentials". Anything else is not: a 503 means the backend
 * could not check them at all — most often because nobody has run `python -m app.seed`, so
 * the `users` table is empty — and the server says so in its own words. Showing the
 * credential message for that would send the operator hunting for a typo that does not
 * exist.
 */
function signInMessage(err: unknown): string {
  if (err instanceof LoginError && err.status !== 401) return err.message
  return 'Sign-in failed. Check your credentials and try again.'
}

export default function LoginPage() {
  const { signIn, session, hydrated } = useAuth()
  const router = useRouter()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Already signed in → go to the role's home (mirrors the Vite <Navigate/>).
  useEffect(() => {
    if (hydrated && session) router.replace(homePathFor(session.fineRole))
  }, [hydrated, session, router])

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const s = await signIn(username || 'analyst', password)
      router.replace(homePathFor(s.fineRole))
    } catch (err) {
      setError(signInMessage(err))
      setBusy(false)
    }
  }

  const quickIn = (name: string): void => {
    setUsername(name)
    setBusy(true)
    setError(null)
    void signIn(name, 'demo')
      .then((s) => router.replace(homePathFor(s.fineRole)))
      .catch((err: unknown) => {
        setError(signInMessage(err))
        setBusy(false)
      })
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      {/* Left: identity / thesis */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-surface p-10 lg:flex">
        <AegisLockup size="lg" />
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
          <AegisLockup size="md" className="mb-8 lg:hidden" />
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Sign in</h1>
          <p className="mb-6 mt-1 text-sm text-muted-foreground">
            Access is role-scoped: the platform admin, a tenant&rsquo;s own admin, AI
            team, DevOps and Client each land in their own portal.
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
                  key={q.username}
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
              Signs in with the backend&apos;s seeded identity for that role. The console
              needs a running backend — every figure it shows is measured, never simulated.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
