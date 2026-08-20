'use client'

import { useRouter } from 'next/navigation'
import { CircleAlert, Loader2, LogIn, ShieldCheck } from 'lucide-react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { useEffect, useState, type FormEvent } from 'react'

import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import { LoginError } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor } from '@/lib/portal'

/**
 * Real sign-in surface. The role returned by `signIn` decides which portal the
 * user lands in (RBAC) — the backend is the only authority on it.
 *
 * **What the design pass changed.** The document outline was upside-down: the
 * identity panel's `h2` came before the form's `h1` and was set larger than it,
 * so the first heading on the page was neither the page's subject nor its top
 * level. The claim is now a paragraph, which is what it always was, and "Sign
 * in" is the only heading.
 *
 * The five demo identities sat in a two-column grid, which leaves the fifth
 * alone in a half-empty row — the tell that the grid was chosen before the
 * content was counted. They are one column of rows now, each naming a role
 * rather than repeating "Enter as" five times down the page.
 *
 * The failure message was a bare red sentence. Colour alone is not a state
 * (DESIGN.md §2), and a sign-in error that a screen reader never announces is
 * an operator stuck on a form that looks unchanged, so it carries an icon, a
 * word and `role="alert"`.
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
const QUICK_IN: { label: string; scope: string; username: string }[] = [
  { label: 'Platform admin', scope: 'operates Aegis itself', username: 'admin' },
  { label: 'Tenant admin', scope: 'administers one tenant', username: 'northwind.admin' },
  { label: 'AI team', scope: 'builds and tunes the agent', username: 'ai' },
  { label: 'DevOps', scope: 'runs the stack', username: 'devops' },
  { label: 'Client', scope: 'the tenant end-user', username: 'client' },
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
  // Which identity is in flight, so the row you pressed is the row that shows it.
  // A page-wide spinner tells you something is happening and not what you chose.
  const [pending, setPending] = useState<string | null>(null)

  // Already signed in → go to the role's home (mirrors the Vite <Navigate/>).
  useEffect(() => {
    if (hydrated && session) router.replace(homePathFor(session.fineRole))
  }, [hydrated, session, router])

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    setBusy(true)
    setPending(null)
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
    setPending(name)
    setError(null)
    void signIn(name, 'demo')
      .then((s) => router.replace(homePathFor(s.fineRole)))
      .catch((err: unknown) => {
        setError(signInMessage(err))
        setBusy(false)
        setPending(null)
      })
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_minmax(28rem,34rem)]">
      {/* Left: identity / thesis. Hidden below lg — it is the claim, not the task. */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-surface p-10 lg:flex xl:p-14">
        <AegisLockup size="lg" />
        <div className="max-w-lg">
          <p className="eyebrow mb-4">Bounded-autonomy AI, made watchable</p>
          <p className="text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] text-balance text-foreground">
            Every autonomous action is uncertainty-bounded, explainable, guarded,
            human-approved, and fully traced.
          </p>
          <p className="mt-5 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            A control room for an agent that takes real actions, with the trust stack
            visible in real time.
          </p>
        </div>
        <p className="flex items-center gap-2 text-muted-foreground">
          <ShieldCheck className="size-4 text-blue-600" aria-hidden />
          <span className="font-mono text-[0.72rem] tracking-wide">
            RBAC · guardrails · SHAP · conformal · OTel audit
          </span>
        </p>
      </aside>

      {/* Right: the task. */}
      <main className="flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-sm">
          <AegisLockup size="md" className="mb-8 lg:hidden" />
          <h1 className="text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-foreground">
            Sign in
          </h1>
          <p className="mt-1 mb-6 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            Access is role-scoped: the platform admin, a tenant&rsquo;s own admin, AI
            team, DevOps and Client each land in their own portal.
          </p>

          <form onSubmit={submit} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <label htmlFor="username" className="block text-[0.8125rem] font-medium text-foreground">
                Username
              </label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="analyst"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                aria-describedby={error ? 'sign-in-error' : undefined}
                className="h-11"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="password" className="block text-[0.8125rem] font-medium text-foreground">
                Password
              </label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                aria-describedby={error ? 'sign-in-error' : undefined}
                className="h-11"
              />
            </div>

            {error ? (
              <p
                id="sign-in-error"
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-block bg-block/10 px-3 py-2.5 text-[0.8125rem] leading-relaxed text-foreground"
              >
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-block-ink" aria-hidden />
                <span>
                  <span className="font-medium">Not signed in.</span> {error}
                </span>
              </p>
            ) : null}

            <Button type="submit" size="lg" className="w-full" disabled={busy}>
              {busy && pending === null ? (
                <>
                  <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
                  Signing in…
                </>
              ) : (
                <>
                  <LogIn className="size-4" aria-hidden /> Sign in
                </>
              )}
            </Button>
          </form>

          <section aria-labelledby="demo-access" className="mt-8 rounded-lg border border-border bg-surface p-4">
            <h2 id="demo-access" className="eyebrow mb-1">
              Demo access
            </h2>
            <p className="mb-3 text-pretty text-[0.8125rem] leading-relaxed text-muted-foreground">
              Signs in with the backend&rsquo;s seeded identity for that role. The console
              needs a running backend — every figure it shows is measured, never simulated.
            </p>
            <ul className="space-y-1.5">
              {QUICK_IN.map((q) => (
                <li key={q.username}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => quickIn(q.username)}
                    className="flex min-h-11 w-full touch-manipulation items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-left outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-[0.8125rem] font-medium text-foreground">
                        {q.label}
                      </span>
                      <span className="block truncate text-xs text-muted-foreground">{q.scope}</span>
                    </span>
                    {pending === q.username ? (
                      <Loader2
                        className="size-4 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
                        aria-hidden
                      />
                    ) : (
                      <span className="tabular shrink-0 font-mono text-[0.68rem] text-muted-foreground">
                        {q.username}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </main>
    </div>
  )
}
