import Link from 'next/link'
import { ShieldHalf, ArrowRight } from 'lucide-react'
import { ROLES, defaultSectionFor, portalLabelFor, type Role } from '@/lib/portal'

/**
 * Login / role-select stub. Real auth (JWT + role from the backend) is wired in
 * a later task; for now, picking a portal routes straight into it so the four
 * role-scoped shells are reachable end-to-end.
 */

const BLURB: Record<Role, string> = {
  admin: 'Oversight, governance & delegation — approvals, tenants, budgets, audit, access.',
  ai_team: 'Build & tune the agent — Console, MLOps, LLMOps, Memory, access demo.',
  devops: 'Run the stack — overview, tech stack & versions, patch check, audit.',
  client: 'The tenant view — value delivered, risk assurance, read-only access demo.',
}

export default function LoginPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col justify-center px-6 py-16">
      <div className="mb-8 flex items-center gap-3">
        <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
          <ShieldHalf className="size-6" />
        </span>
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Aegis Console</h1>
          <p className="text-sm text-muted-foreground">Choose a portal to enter</p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {ROLES.map((role) => (
          <Link
            key={role}
            href={`/app/${role}/${defaultSectionFor(role)}`}
            className="group flex flex-col gap-2 rounded-2xl border border-border bg-card p-5 shadow-card transition-shadow hover:shadow-hover"
          >
            <div className="flex items-center justify-between">
              <span className="t-title text-foreground">{portalLabelFor(role)}</span>
              <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
            </div>
            <p className="text-sm text-muted-foreground">{BLURB[role]}</p>
          </Link>
        ))}
      </div>

      <p className="mt-8 text-xs text-muted-foreground">
        Auth is a stub in this scaffold — role selection routes directly into each portal shell.
      </p>
    </main>
  )
}
