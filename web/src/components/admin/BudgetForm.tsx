'use client'

import { Loader2, Wallet } from 'lucide-react'
import { useState, type FormEvent, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Button } from '@/components/primitives/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { createBudget } from '@/lib/api/client'
import type { AdminUser, Budget, BudgetScope, Tenant } from '@/lib/api/types'

import {
  WINDOWS,
  budgetBody,
  checkBudgetDraft,
  capRefusalWarning,
  isWellFormed,
  numberOrNull,
  positiveIntOrNull,
  refusalSentence,
  scopeVerdict,
  writableScopes,
  type AdminTier,
  type BudgetDraft,
} from './adminForms'
import { NotYours, Outcome, SelectField, TextField, type FormOutcome } from './formBits'

/** A fresh draft, on the scope this tier is actually allowed to write. */
function blankDraft(tier: AdminTier): BudgetDraft {
  return {
    scopeType: writableScopes(tier)[0] ?? 'user',
    scopeId: '',
    window: 'day',
    usdCap: '',
    tokenCap: '',
    rpm: '',
    tpm: '',
  }
}

/**
 * Set a cap — `POST /admin/budgets`, the client function that until now was called
 * from nowhere, which is exactly why there was no budget form.
 *
 * **Who may set what is a server rule reflected here, never invented here.** §7.16
 * row 2 makes a tenant's own cap `writable_by: platform`: a tenant admin who could
 * raise it could raise it to anything, so the scope picker offers them users only and
 * the tenant scope appears as a named, locked control with its reason. The route
 * agrees — `_resolve_budget_tenant` answers 403 for a tenant-scoped write that is not
 * their own tenant, and for a user in someone else's.
 *
 * One honesty note the form owes the operator: `effective_limits` clamps a user cap
 * *inward* to its tenant's, so a sub-cap above the tenant cap saves and then never
 * binds. That is an advisory, not a refusal — the backend accepts it — and it is
 * said before the write rather than discovered afterwards.
 */
export function BudgetForm({
  token,
  tier,
  tenants,
  users,
  budgets,
  onSaved,
}: {
  token: string | null
  tier: AdminTier
  tenants: readonly Tenant[]
  users: readonly AdminUser[]
  /** Caps already in force, used to find the tenant cap governing a chosen user. */
  budgets: readonly Budget[]
  onSaved: (budget: Budget) => void
}): ReactElement {
  const [draft, setDraft] = useState<BudgetDraft>(() => blankDraft(tier))
  const [submitted, setSubmitted] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<FormOutcome>(null)

  const problems = checkBudgetDraft(draft, tier)
  const shown = submitted ? problems : {}
  const scopes = writableScopes(tier)
  const tenantScopeVerdict = scopeVerdict('tenant', tier)

  // The tenant cap that will clamp this user cap, when both are known.
  const chosenUser =
    draft.scopeType === 'user' ? users.find((u) => u.id === positiveIntOrNull(draft.scopeId)) : undefined
  const governingTenantCap =
    chosenUser?.tenant_id != null
      ? (budgets.find(
          (b) => b.scope_type === 'tenant' && b.scope_id === chosenUser.tenant_id && b.window === draft.window,
        )?.usd_cap ?? null)
      : null
  const advisory = capRefusalWarning(numberOrNull(draft.usdCap), governingTenantCap)

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    setSubmitted(true)
    if (!isWellFormed(problems) || busy) return
    setBusy(true)
    setOutcome(null)
    createBudget(budgetBody(draft), token).then(
      (saved) => {
        setBusy(false)
        setOutcome({ kind: 'created', message: savedSentence(saved, tenants, users) })
        setSubmitted(false)
        onSaved(saved)
      },
      (error: unknown) => {
        setBusy(false)
        setOutcome({ kind: 'refused', message: refusalSentence(error) })
      },
    )
  }

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <Wallet className="size-4 text-ml" aria-hidden />
        <CardTitle>Set a budget</CardTitle>
        <Badge variant="outline">{tier === 'platform' ? 'tenants and users' : 'your users'}</Badge>
      </CardHeader>
      <CardContent>
        {tier === 'none' ? (
          <NotYours label="Setting a cap" reason="Only an admin sets budgets." />
        ) : (
          <form onSubmit={submit} noValidate className="flex flex-col gap-4">
            {!tenantScopeVerdict.writable && tenantScopeVerdict.reason != null && (
              <NotYours label="Your tenant’s own cap" reason={tenantScopeVerdict.reason} />
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <SelectField
                id="budget-scope"
                label="This caps"
                problem={shown.scopeType}
                value={draft.scopeType}
                onChange={(e) =>
                  setDraft({ ...draft, scopeType: e.target.value as BudgetScope, scopeId: '' })
                }
              >
                {scopes.map((s) => (
                  <option key={s} value={s}>
                    {s === 'tenant' ? 'A tenant' : 'A user'}
                  </option>
                ))}
              </SelectField>

              <SelectField
                id="budget-target"
                label={draft.scopeType === 'tenant' ? 'Tenant' : 'User'}
                problem={shown.scopeId}
                value={draft.scopeId}
                onChange={(e) => setDraft({ ...draft, scopeId: e.target.value })}
              >
                <option value="">Choose one…</option>
                {draft.scopeType === 'tenant'
                  ? tenants.map((t) => (
                      <option key={t.id} value={String(t.id)}>
                        {t.name} · #{t.id}
                      </option>
                    ))
                  : users.map((u) => (
                      <option key={u.id} value={String(u.id)}>
                        {u.username} · #{u.id}
                        {u.tenant_id != null ? ` · t#${u.tenant_id}` : ''}
                      </option>
                    ))}
              </SelectField>
            </div>

            <SelectField
              id="budget-window"
              label="Cap resets every"
              hint="Re-posting the same scope and window adjusts that cap rather than adding a second one."
              value={draft.window}
              className="sm:max-w-[14rem]"
              onChange={(e) => setDraft({ ...draft, window: e.target.value as BudgetDraft['window'] })}
            >
              {WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {w === 'day' ? 'Day' : 'Month'}
                </option>
              ))}
            </SelectField>

            <fieldset>
              <legend className="mb-2 text-[0.78rem] font-medium text-foreground">
                Caps — leave a box blank to leave that dimension uncapped
              </legend>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <TextField
                  id="budget-usd"
                  label="Spend (USD)"
                  problem={shown.usdCap}
                  value={draft.usdCap}
                  inputMode="decimal"
                  onChange={(e) => setDraft({ ...draft, usdCap: e.target.value })}
                  placeholder="50"
                />
                <TextField
                  id="budget-tokens"
                  label="Tokens"
                  problem={shown.tokenCap}
                  value={draft.tokenCap}
                  inputMode="numeric"
                  onChange={(e) => setDraft({ ...draft, tokenCap: e.target.value })}
                  placeholder="200000"
                />
                <TextField
                  id="budget-rpm"
                  label="Requests a minute"
                  problem={shown.rpm}
                  value={draft.rpm}
                  inputMode="numeric"
                  onChange={(e) => setDraft({ ...draft, rpm: e.target.value })}
                  placeholder="60"
                />
                <TextField
                  id="budget-tpm"
                  label="Tokens a minute"
                  problem={shown.tpm}
                  value={draft.tpm}
                  inputMode="numeric"
                  onChange={(e) => setDraft({ ...draft, tpm: e.target.value })}
                  placeholder="40000"
                />
              </div>
            </fieldset>

            {advisory != null && (
              <p className="rounded-lg border border-border bg-surface-2/50 px-3 py-2 text-[0.72rem] text-muted-foreground">
                {advisory}
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />}
                {busy ? 'Saving…' : 'Set budget'}
              </Button>
            </div>

            <Outcome outcome={outcome} />
          </form>
        )}
      </CardContent>
    </Card>
  )
}

/** What a saved budget says it now governs, named rather than numbered. */
function savedSentence(
  saved: Budget,
  tenants: readonly Tenant[],
  users: readonly AdminUser[],
): string {
  const who =
    saved.scope_type === 'tenant'
      ? (tenants.find((t) => t.id === saved.scope_id)?.name ?? `tenant #${saved.scope_id}`)
      : (users.find((u) => u.id === saved.scope_id)?.username ?? `user #${saved.scope_id}`)
  const caps = [
    saved.usd_cap != null ? `$${saved.usd_cap}` : null,
    saved.token_cap != null ? `${saved.token_cap} tokens` : null,
    saved.rpm != null ? `${saved.rpm} rpm` : null,
    saved.tpm != null ? `${saved.tpm} tpm` : null,
  ].filter((c): c is string => c !== null)
  return `Set. ${who} is capped at ${caps.join(', ')} a ${saved.window}, and the gateway enforces it on the next call.`
}
