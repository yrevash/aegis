'use client'

import { Loader2, UserPlus } from 'lucide-react'
import { useState, type FormEvent, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { createUser } from '@/lib/api/client'
import type { AdminUser, Tenant } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

import {
  ASSIGNABLE_ROLES,
  MIN_PASSWORD,
  canChooseTenant,
  checkUserDraft,
  isWellFormed,
  refusalSentence,
  userBody,
  type AdminTier,
  type UserDraft,
} from './adminForms'
import { NotYours, Outcome, SelectField, TextField, type FormOutcome } from './formBits'
import { ROLE_CATALOG } from './roleCatalog'

/** A fresh draft — the client portal is the safe default, not the admin one. */
function blankDraft(): UserDraft {
  return { username: '', role: 'client', tenantId: '', email: '', password: '' }
}

/**
 * Provision a user — `POST /admin/users`.
 *
 * The tenant picker exists **only for a platform admin**. `admin_create_user` pins a
 * tenant admin to `auth.tenant_id` and answers 403 for any other tenant, so offering
 * them the field would be offering a control whose only outcome is a refusal; they
 * get a line naming the tenant the user will land in instead.
 *
 * The password is required here even though the schema allows it to be null, because
 * the acceptance test for this form is signing out and signing back in as the user it
 * just created — which a user with no password cannot do.
 */
export function CreateUserForm({
  token,
  tier,
  tenants,
  ownTenantId,
  onCreated,
}: {
  token: string | null
  tier: AdminTier
  /** Every tenant, for the platform admin's picker. Empty for a tenant admin. */
  tenants: readonly Tenant[]
  /** The tenant a tenant admin is pinned to, for the caption. */
  ownTenantId: number | null
  onCreated: (user: AdminUser) => void
}): ReactElement {
  const [draft, setDraft] = useState<UserDraft>(blankDraft)
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<FormOutcome>(null)

  const problems = checkUserDraft(draft, tier)
  const shown = submitted ? problems : {}
  const picksTenant = canChooseTenant(tier)

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    setSubmitted(true)
    if (!isWellFormed(problems) || busy) return
    setBusy(true)
    setOutcome(null)
    createUser(userBody(draft, tier), token).then(
      (user) => {
        setBusy(false)
        setOutcome({
          kind: 'created',
          message: `Created. ${user.username} is user #${user.id}${
            user.tenant_id != null ? ` in tenant #${user.tenant_id}` : ' at the platform scope'
          }, and can sign in now with the password you set.`,
        })
        setDraft(blankDraft())
        setSubmitted(false)
        onCreated(user)
      },
      (error: unknown) => {
        setBusy(false)
        // A cross-tenant attempt is the isolation rule showing its work. The backend's
        // sentence says exactly that; replacing it would throw the story away.
        setOutcome({ kind: 'refused', message: refusalSentence(error) })
      },
    )
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <UserPlus className="size-4 text-blue-700" aria-hidden />
        <CardTitle>Create a user</CardTitle>
        <Badge variant="outline">
          {tier === 'platform'
            ? 'any tenant'
            : ownTenantId != null
              ? `tenant #${ownTenantId}`
              : 'your tenant'}
        </Badge>
      </CardHeader>
      <CardContent>
        {tier === 'none' ? (
          <NotYours label="Provisioning a user" reason="Only an admin provisions users." />
        ) : (
          <form onSubmit={submit} noValidate className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                id="user-name"
                label="Username"
                hint="What they sign in with. It must be unique across the platform."
                problem={shown.username}
                value={draft.username}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                placeholder="a.rao"
              />
              <TextField
                id="user-password"
                label="Password"
                hint={`At least ${MIN_PASSWORD} characters. Argon2-hashed on write — it is never stored as typed.`}
                problem={shown.password}
                type="password"
                value={draft.password}
                autoComplete="new-password"
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
              />
              <SelectField
                id="user-role"
                label="Portal"
                hint={ROLE_CATALOG[draft.role].sees}
                problem={shown.role}
                value={draft.role}
                onChange={(e) => setDraft({ ...draft, role: e.target.value as Role })}
              >
                {ASSIGNABLE_ROLES.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_CATALOG[role].label}
                  </option>
                ))}
              </SelectField>
              <TextField
                id="user-email"
                label="Email (optional)"
                hint="Contact only. Sign-in is by username."
                problem={shown.email}
                type="email"
                value={draft.email}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
              />
            </div>

            {picksTenant ? (
              <SelectField
                id="user-tenant"
                label="Tenant"
                hint="Leave on the platform scope for an Aegis operator who belongs to no tenant."
                problem={shown.tenantId}
                value={draft.tenantId}
                className="sm:max-w-[20rem]"
                onChange={(e) => setDraft({ ...draft, tenantId: e.target.value })}
              >
                <option value="">Platform scope — no tenant</option>
                {tenants.map((t) => (
                  <option key={t.id} value={String(t.id)}>
                    {t.name} · #{t.id}
                  </option>
                ))}
              </SelectField>
            ) : (
              <NotYours
                label="Tenant"
                reason={
                  ownTenantId != null
                    ? `Pinned to tenant #${ownTenantId}. Aegis fills this in from your sign-in — a user created here can only be yours.`
                    : 'Pinned to your own tenant. Aegis fills this in from your sign-in.'
                }
              />
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                {busy ? 'Creating…' : 'Create user'}
              </Button>
            </div>

            <Outcome outcome={outcome} />
          </form>
        )}
      </CardContent>
    </Card>
  )
}
