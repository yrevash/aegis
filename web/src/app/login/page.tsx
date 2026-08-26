'use client'

import { useRouter } from 'next/navigation'
import { CircleAlert, Loader2, LogIn, ShieldCheck } from 'lucide-react'

import { AegisLockup } from '@/components/brand/AegisLockup'
import { useEffect, useState, type FormEvent } from 'react'

import { LandingScene } from '@/components/landing/LandingScene'
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
 *
 * **The split now reads as a split.** The identity panel was `bg-surface` —
 * `#ffffff`, the same white as the card beside it — so the two-column shell
 * rendered as one undifferentiated sheet with a hairline down the middle. It is
 * the blue-tinted canvas now and the task side is the white surface, which is
 * the console's own figure-and-ground relationship rather than a new one
 * invented for this page.
 *
 * The canvas is also the only ground the scene works on: every Storyset file
 * carries its own near-white background plate, so the navy this panel was first
 * drafted as turned the drawing into a pale rectangle pasted onto the panel.
 *
 * **Sign in is blue, not near-black.** A visitor arrives here from a landing page
 * whose two calls to action are `--blue-600`, and `--primary` is shadcn's
 * near-black default rather than this product's action colour. The override is
 * local because `--primary` is shared with every console screen; the token itself
 * is a decision for whoever owns `globals.css`.
 */

/**
 * Quick-in identities. Each is a real `users` row written by `python -m app.seed`
 * (`backend/src/app/seed.py`, password `demo` unless `AEGIS_SEED_PASSWORD` says
 * otherwise), so these buttons issue an ordinary `POST /auth/login` like the form does.
 * An unseeded backend answers 503 and says to run the seed — there is no fallback
 * login table behind these names.
 *
 * **Tenant-bound accounts, not the un-tenanted ones.** This list used to offer `ai`
 * and `client` — seed rows with `tenant_id = NULL`. They log in, but every
 * tenant-scoped screen is then correctly empty, because there is no tenant to scope
 * to. The client overview showed a dash for "Your spend" while `northwind.client`
 * had **2,653** ledger rows sitting behind it, and that read as broken software
 * rather than as the wrong account.
 *
 * **Both tenants are offered on purpose.** Signing in as Northwind's client and
 * then Vertex's client shows two genuinely different sets of figures from one
 * database, which is the platform's central claim and is not demonstrable from a
 * single account. The un-tenanted `admin` stays — it is the *platform* operator,
 * a different job from a tenant administrator, with different screens.
 *
 * Which portal each lands in is decided by the backend's `fine_role`, never by this
 * list.
 */
const QUICK_IN: { label: string; scope: string; username: string }[] = [
  { label: 'Platform admin', scope: 'operates Aegis itself · every tenant', username: 'admin' },
  { label: 'Northwind · tenant admin', scope: 'administers tenant 1', username: 'northwind.admin' },
  { label: 'Northwind · client', scope: 'end-user, tenant 1', username: 'northwind.client' },
  { label: 'Northwind · AI team', scope: 'builds and tunes the agent', username: 'northwind.analyst' },
  { label: 'Vertex · tenant admin', scope: 'administers tenant 2', username: 'vertex.admin' },
  { label: 'Vertex · client', scope: 'end-user, tenant 2 — different data', username: 'vertex.client' },
  { label: 'DevOps', scope: 'runs the stack · platform-wide', username: 'devops' },
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
      {/* Left: what is on the other side. Hidden below lg — it is the claim, not
          the task, and a phone should open on the form. */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-background p-10 lg:flex xl:p-14">
        <AegisLockup size="lg" />
        <div className="max-w-lg">
          <LandingScene name="locked" width={420} className="mb-10" />
          <p className="eyebrow mb-4">Autonomy you can audit</p>
          <p className="font-display text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] text-balance text-foreground">
            Every action leaves a record you can read.
          </p>
          <p className="mt-5 max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            Guardrails on the way in and on the way out, a person on the consequential
            writes, and a source line under every figure.
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
      <main className="flex items-center justify-center bg-surface px-5 py-10 sm:px-8">
        <div className="w-full max-w-sm">
          <AegisLockup size="md" className="mb-8 lg:hidden" />
          <h1 className="font-display text-[1.75rem] leading-8 font-semibold tracking-[-0.02em] text-foreground">
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

            <Button
              type="submit"
              size="lg"
              className="w-full bg-blue-600 text-white hover:bg-blue-700"
              disabled={busy}
            >
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

          <section aria-labelledby="demo-access" className="mt-8 rounded-lg border border-border bg-surface-2 p-4">
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
                    className="flex min-h-11 w-full touch-manipulation items-center gap-3 rounded-lg border border-border bg-card px-3 py-2 text-left outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
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
                      <span className="tabular shrink-0 font-mono text-[0.6875rem] text-muted-foreground">
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
