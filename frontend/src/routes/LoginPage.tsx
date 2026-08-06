import { LogIn, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent, type ReactElement } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { useAuth } from '@/auth/AuthContext'
import { homePathFor } from '@/auth/RequireRole'
import { Wordmark } from '@/components/brand/Logo'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useBackendMode } from '@/state/backendMode'

/**
 * The sign-in surface. On success the returned role decides which portal the
 * user lands in (RBAC). In demo mode any username works; one containing "admin"
 * yields the admin portal.
 */
export function LoginPage(): ReactElement {
  const { signIn, session } = useAuth()
  const { mode } = useBackendMode()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (session) {
    return <Navigate to={homePathFor(session.role)} replace />
  }

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const s = await signIn(username || 'analyst', password)
      navigate(homePathFor(s.role), { replace: true })
    } catch {
      setError('Sign-in failed. Check your credentials and try again.')
    } finally {
      setBusy(false)
    }
  }

  const quickIn = (name: string): void => {
    setUsername(name)
    void signIn(name, 'demo').then((s) => navigate(homePathFor(s.role), { replace: true }))
  }

  return (
    <div className="grid h-full overflow-y-auto lg:grid-cols-2">
      {/* Left: identity / thesis */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border/80 bg-card/40 p-10 lg:flex">
        <Wordmark />
        <div className="max-w-md">
          <p className="eyebrow mb-4">Bounded-autonomy AI, made watchable</p>
          <h2 className="font-display text-3xl leading-tight font-semibold text-foreground">
            Every autonomous action is{' '}
            <span className="text-ml-ink">uncertainty-bounded</span>,{' '}
            <span className="text-ml-ink">explainable</span>, <span className="text-block-ink">guarded</span>,{' '}
            <span className="text-risk-ink">human-approved</span>, and{' '}
            <span className="text-agent-ink">fully traced</span>.
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            A control room for an agent that takes real actions — with the trust
            stack visible in real time.
          </p>
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <ShieldCheck className="size-4 text-ok" />
          <span className="font-mono text-[0.72rem] tracking-wide">
            RBAC · guardrails · SHAP · conformal · OTel audit
          </span>
        </div>
      </aside>

      {/* Right: form */}
      <main className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Wordmark />
          </div>
          <h1 className="font-display text-2xl font-semibold">Sign in</h1>
          <p className="mt-1 mb-6 text-sm text-muted-foreground">
            Access is role-scoped: admins and users land in separate portals.
          </p>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="analyst"
                autoComplete="username"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>
            {error && <p className="text-sm text-block-ink">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>
              <LogIn className="size-4" /> Sign in
            </Button>
          </form>

          {mode === 'mock' && (
            <div className="mt-6 rounded-lg border border-border/70 bg-surface/40 p-3">
              <p className="eyebrow mb-2">Demo access</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="flex-1" onClick={() => quickIn('analyst')}>
                  Enter as user
                </Button>
                <Button variant="outline" size="sm" className="flex-1" onClick={() => quickIn('admin')}>
                  Enter as admin
                </Button>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
