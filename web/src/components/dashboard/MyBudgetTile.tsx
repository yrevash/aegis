'use client'

import { Wallet } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { StatCard } from '@/components/metrics/StatCard'
import { getMyBudget, type MyBudgetResponse } from '@/lib/api/console'

/**
 * The caller's **own** spend against their **own** cap.
 *
 * Until `GET /me/budget` existed, a `client`-role user could not see this anywhere:
 * `/admin/budgets` and `/governance/dashboard` are both behind `require_tenant_admin`,
 * so the role the product exists for was refused runs on a number it was never shown.
 *
 * The figures come from the same `BudgetStatusRow`s the gateway enforcer compares a
 * call against, so the tile and the refusal can never disagree — and when **no cap
 * governs the caller** (`measured: false`) it says so rather than drawing a zero. An
 * unmeasured figure presented as a measurement is the one failure this surface is not
 * allowed to have.
 */
export function MyBudgetTile({ token }: { token: string | null }): ReactElement {
  const [budget, setBudget] = useState<MyBudgetResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    getMyBudget(token)
      .then((data) => {
        if (alive) {
          setBudget(data)
          setFailed(false)
        }
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
    return () => {
      alive = false
    }
  }, [token])

  const capped = budget != null && budget.measured && budget.usd_cap != null
  const info = failed ? (
    'Your budget could not be read. Nothing is drawn rather than a figure that might be wrong.'
  ) : budget != null && !budget.measured ? (
    'No spend cap governs this account yet, so there is nothing to measure. A tenant admin sets one under Governance.'
  ) : (
    'Your spend over the cap’s own rolling window, read from the same budget rows the gateway enforcer compares every call against — so this tile and a refusal can never disagree.'
  )

  return (
    <StatCard
      label="Your spend"
      value={capped ? budget.cost_usd_used : null}
      format={(n) =>
        capped && budget.usd_cap != null
          ? `$${n.toFixed(2)} of $${budget.usd_cap.toFixed(0)}`
          : `$${n.toFixed(2)}`
      }
      icon={Wallet}
      signal="risk"
      live={capped}
      info={info}
    />
  )
}
