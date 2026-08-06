import { Loader2, Plus, Wallet } from 'lucide-react'
import { useState, type FormEvent, type ReactElement, type ReactNode } from 'react'
import { toast } from 'sonner'

import { createBudget, getBudgets, getUsage } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { InfoTip } from '@/components/ui/InfoTip'
import { Label } from '@/components/ui/label'
import { formatUsd } from '@/components/dashboard/roi'
import { budgetUtilisation } from './governance'
import type {
  Budget,
  BudgetScope,
  BudgetWindow,
  BudgetsResponse,
  CreateBudgetRequest,
  UsageResponse,
} from '@/types/api'

import { useAsync } from './useAsync'

/** Parse a numeric input to a number, or null when blank. */
function numOrNull(v: string): number | null {
  const t = v.trim()
  if (t === '') return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** Compact cap formatter (null → "—"). */
function cap(v: number | null, prefix = ''): string {
  if (v == null) return '—'
  return `${prefix}${v.toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 1 })}`
}

/** Utilisation → meter hue: calm until it nears the cap, then warns. */
function meterHex(frac: number): string {
  if (frac >= 0.9) return 'var(--block-ink)'
  if (frac >= 0.7) return 'var(--risk-ink)'
  return 'var(--ok-ink)'
}

const EMPTY_FORM = {
  scope_type: 'tenant' as BudgetScope,
  scope_id: '',
  window: 'month' as BudgetWindow,
  token_cap: '',
  usd_cap: '',
  rpm: '',
  tpm: '',
}

/**
 * Budgets — the spend and rate caps that bound cost before a model is ever
 * called. View the hierarchical caps (with a utilisation meter where spend is
 * measurable) and create a new one per tenant / user. Mirrors
 * `GET/POST /admin/budgets`; the enforcement detail lives in a tooltip.
 */
export function BudgetsView({ token }: { token: string | null }): ReactElement {
  const { state, reload } = useAsync<BudgetsResponse>(() => getBudgets(token), [token])
  const usage = useAsync<UsageResponse>(() => getUsage(token, { window: 'month' }), [token])
  const [form, setForm] = useState(EMPTY_FORM)
  const [busy, setBusy] = useState(false)

  const monthSpend = usage.state.status === 'ready' ? usage.state.data.total_cost_usd : null

  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]): void =>
    setForm((f) => ({ ...f, [k]: v }))

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault()
    const scopeId = numOrNull(form.scope_id)
    if (scopeId == null) {
      toast.error('Scope id is required', { description: 'Enter the tenant or user id to cap.' })
      return
    }
    const body: CreateBudgetRequest = {
      scope_type: form.scope_type,
      scope_id: scopeId,
      window: form.window,
      token_cap: numOrNull(form.token_cap),
      usd_cap: numOrNull(form.usd_cap),
      rpm: numOrNull(form.rpm),
      tpm: numOrNull(form.tpm),
    }
    setBusy(true)
    try {
      await createBudget(body, token)
      toast.success('Budget saved', {
        description: `${body.scope_type} #${body.scope_id} · ${body.window}`,
      })
      setForm(EMPTY_FORM)
      reload()
    } catch (error) {
      toast.error('Could not save the budget', {
        description: error instanceof Error ? error.message : undefined,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <Wallet className="size-4 text-ml-ink" />
          <CardTitle>Budgets &amp; rate caps</CardTitle>
          <InfoTip label="How caps are enforced">
            Enforced on every request — a call is blocked once any level along its tenant → user
            path is over budget.
          </InfoTip>
          {state.status === 'ready' && (
            <Badge variant="secondary" className="ml-auto">
              {state.data.rows.length} caps
            </Badge>
          )}
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {state.status === 'loading' && (
            <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading budgets…
            </div>
          )}
          {state.status === 'error' && (
            <div className="py-10 text-sm text-block-ink">Could not load budgets. {state.message}</div>
          )}
          {state.status === 'ready' && state.data.rows.length === 0 && (
            <div className="py-10 text-sm text-muted-foreground">No budgets set. Create one on the right.</div>
          )}
          {state.status === 'ready' && state.data.rows.length > 0 && (
            <table className="w-full min-w-[620px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  <Th>Scope</Th>
                  <Th>Window</Th>
                  <Th
                    info="Month-to-date spend against the USD cap. Spend is the current tenant's measured usage; day-window and uncapped rows show no meter."
                  >
                    USD used
                  </Th>
                  <Th>Tokens</Th>
                  <Th info="Requests per minute — the rate ceiling for this scope.">RPM</Th>
                  <Th info="Tokens per minute — the throughput ceiling for this scope.">TPM</Th>
                </tr>
              </thead>
              <tbody>
                {state.data.rows.map((b, i) => (
                  <tr
                    key={b.id ?? i}
                    className="animate-trace-in border-b border-border/40 transition-colors last:border-0 hover:bg-surface-2/50"
                    style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
                  >
                    <td className="py-2.5">
                      <Badge variant={b.scope_type === 'tenant' ? 'graph' : 'agent'}>
                        {b.scope_type} #{b.scope_id}
                      </Badge>
                    </td>
                    <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">{b.window}</td>
                    <td className="py-2.5">
                      <UsdCell budget={b} monthSpend={monthSpend} />
                    </td>
                    <td className="tabular py-2.5 font-mono text-[0.72rem]">{cap(b.token_cap)}</td>
                    <td className="tabular py-2.5 font-mono text-[0.72rem]">{cap(b.rpm)}</td>
                    <td className="tabular py-2.5 font-mono text-[0.72rem]">{cap(b.tpm)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <Plus className="size-4 text-ok-ink" />
          <CardTitle>New budget</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Scope">
                <select
                  value={form.scope_type}
                  onChange={(e) => set('scope_type', e.target.value as BudgetScope)}
                  className="h-9 w-full rounded-lg border border-input bg-surface px-2 text-sm"
                >
                  <option value="tenant">tenant</option>
                  <option value="user">user</option>
                </select>
              </Field>
              <Field label="Scope id">
                <Input
                  value={form.scope_id}
                  onChange={(e) => set('scope_id', e.target.value)}
                  inputMode="numeric"
                  placeholder="2"
                />
              </Field>
            </div>
            <Field label="Window">
              <select
                value={form.window}
                onChange={(e) => set('window', e.target.value as BudgetWindow)}
                className="h-9 w-full rounded-lg border border-input bg-surface px-2 text-sm"
              >
                <option value="day">day</option>
                <option value="month">month</option>
              </select>
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Token cap">
                <Input value={form.token_cap} onChange={(e) => set('token_cap', e.target.value)} inputMode="numeric" placeholder="5000000" />
              </Field>
              <Field label="USD cap">
                <Input value={form.usd_cap} onChange={(e) => set('usd_cap', e.target.value)} inputMode="decimal" placeholder="1200" />
              </Field>
              <Field label="RPM">
                <Input value={form.rpm} onChange={(e) => set('rpm', e.target.value)} inputMode="numeric" placeholder="600" />
              </Field>
              <Field label="TPM">
                <Input value={form.tpm} onChange={(e) => set('tpm', e.target.value)} inputMode="numeric" placeholder="200000" />
              </Field>
            </div>
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              Save budget
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

/** The USD-cap cell: cap value plus a utilisation meter where spend is real. */
function UsdCell({ budget, monthSpend }: { budget: Budget; monthSpend: number | null }): ReactElement {
  const showMeter = budget.window === 'month' && budget.usd_cap != null && monthSpend != null
  const frac = showMeter ? budgetUtilisation(monthSpend, budget.usd_cap) : null

  if (budget.usd_cap == null) {
    return <span className="tabular font-mono text-[0.72rem] text-muted-foreground">—</span>
  }
  return (
    <div className="min-w-[7rem]">
      <div className="flex items-baseline justify-between gap-2">
        <span className="tabular font-mono text-[0.72rem] text-foreground">
          {frac != null && monthSpend != null ? formatUsd(monthSpend) : '—'}
        </span>
        <span className="tabular font-mono text-[0.68rem] text-muted-foreground">/ {cap(budget.usd_cap, '$')}</span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        {frac != null && (
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${Math.max(2, Math.round(frac * 100))}%`, background: meterHex(frac) }}
          />
        )}
      </div>
    </div>
  )
}

/** A table header cell in the eyebrow style, optionally with an InfoTip. */
function Th({ children, info }: { children: ReactNode; info?: string }): ReactElement {
  return (
    <th className="pb-2 font-normal">
      <span className="inline-flex items-center gap-1">
        <span className="eyebrow">{children}</span>
        {info && <InfoTip label="More information">{info}</InfoTip>}
      </span>
    </th>
  )
}

/** A labelled form field. */
function Field({ label, children }: { label: string; children: ReactElement }): ReactElement {
  return (
    <div className="space-y-1">
      <Label className="text-[0.72rem]">{label}</Label>
      {children}
    </div>
  )
}
