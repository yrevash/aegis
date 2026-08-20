'use client'

import { Building2, Loader2 } from 'lucide-react'
import { useState, type FormEvent, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { createTenant } from '@/lib/api/client'
import type { Tenant } from '@/lib/api/types'

import {
  WINDOWS,
  checkTenantDraft,
  isWellFormed,
  refusalSentence,
  tenantBody,
  type AdminTier,
  type TenantDraft,
} from './adminForms'
import { NotYours, Outcome, SelectField, TextField, type FormOutcome } from './formBits'

/**
 * Onboard a tenant — `POST /admin/tenants`.
 *
 * **The spend cap is a first-class field, not an afterthought.** The route answers
 * 422 without one and for a cap of zero, and the reason is worth stating on the
 * screen: an absent `budgets` row means uncapped, so a tenant onboarded without a
 * cap would spend without limit and the omission would surface as a bill rather than
 * as an error. The form says so under the field.
 *
 * Creating a tenant is platform-only (`require_platform_admin`). A tenant admin sees
 * the card with the reason it is not theirs, rather than a control that would 403.
 *
 * After a success the new tenant is handed up (`onCreated`) so the tenant list, the
 * user form's tenant picker and the budget form's scope picker all show it at once.
 */
export function CreateTenantForm({
  token,
  tier,
  onCreated,
}: {
  token: string | null
  tier: AdminTier
  onCreated: (tenant: Tenant) => void
}): ReactElement {
  const [draft, setDraft] = useState<TenantDraft>({ name: '', usdCap: '', window: 'day' })
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<FormOutcome>(null)

  const problems = checkTenantDraft(draft)
  // Problems appear once the operator has tried to submit, not while they are still
  // typing the first character of a name.
  const shown = submitted ? problems : {}

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    setSubmitted(true)
    if (!isWellFormed(problems) || busy) return
    setBusy(true)
    setOutcome(null)
    createTenant(tenantBody(draft), token).then(
      (tenant) => {
        setBusy(false)
        setOutcome({
          kind: 'created',
          message: `Created. ${tenant.name} is tenant #${tenant.id}, capped at $${tenantBody(draft).usd_cap} a ${draft.window}.`,
        })
        setDraft({ name: '', usdCap: '', window: 'day' })
        setSubmitted(false)
        onCreated(tenant)
      },
      (error: unknown) => {
        setBusy(false)
        // The server's own sentence — a 409 names the tenant that already exists and
        // a 422 names the field it refused.
        setOutcome({ kind: 'refused', message: refusalSentence(error) })
      },
    )
  }

  return (
    <Card className="rounded-lg">
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <Building2 className="size-4 text-blue-600" aria-hidden />
        <CardTitle>Create a tenant</CardTitle>
        <Badge variant="outline">platform only</Badge>
      </CardHeader>
      <CardContent>
        {tier !== 'platform' ? (
          <NotYours
            label="Onboarding a tenant"
            reason="Aegis onboards tenants. Your admin rights end at your own tenant’s users and their caps."
          />
        ) : (
          <form onSubmit={submit} noValidate className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                id="tenant-name"
                label="Tenant name"
                hint="How this client appears everywhere else. It must be unique."
                problem={shown.name}
                value={draft.name}
                autoComplete="off"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Acme Manufacturing"
              />
              <TextField
                id="tenant-cap"
                label="Spend cap (USD)"
                hint="Required. Without a cap the tenant is uncapped, and you would learn that from the bill."
                problem={shown.usdCap}
                value={draft.usdCap}
                inputMode="decimal"
                onChange={(e) => setDraft({ ...draft, usdCap: e.target.value })}
                placeholder="500"
              />
            </div>
            <SelectField
              id="tenant-window"
              label="Cap resets every"
              hint="The accounting window the cap runs over."
              value={draft.window}
              className="sm:max-w-[14rem]"
              onChange={(e) => setDraft({ ...draft, window: e.target.value as TenantDraft['window'] })}
            >
              {WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {w === 'day' ? 'Day' : 'Month'}
                </option>
              ))}
            </SelectField>

            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                {busy ? 'Creating…' : 'Create tenant'}
              </Button>
            </div>

            <Outcome outcome={outcome} />
          </form>
        )}
      </CardContent>
    </Card>
  )
}
