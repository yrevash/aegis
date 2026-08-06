import { BarChart3, Building2, ShieldCheck, Users, Wallet } from 'lucide-react'
import type { ReactElement } from 'react'

import { InfoTip } from '@/components/ui/InfoTip'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

import { BudgetsView } from './BudgetsView'
import { GovernanceOverview } from './GovernanceOverview'
import { TenantsView } from './TenantsView'
import { UsageView } from './UsageView'
import { UsersView } from './UsersView'

/**
 * Aegis Governance — the admin surface for the estate: tenants, users, spend
 * caps and usage. Opens on a live overview band (§4.6) so the state of the
 * estate reads before any table, then keeps the four tabs. The "who / capped
 * how / spending what" framing lives in tooltips, not prose on the face.
 */
export function AdminSettings({ token }: { token: string | null }): ReactElement {
  return (
    <div className="flex flex-col gap-5">
      <header className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-graph/12">
          <ShieldCheck className="size-4.5 text-graph-ink" />
        </span>
        <div className="flex flex-col leading-tight">
          <h1 className="t-title text-foreground">Aegis Governance</h1>
          <span className="font-mono text-[0.68rem] text-muted-foreground">
            Tenants · budgets · usage · RBAC
          </span>
        </div>
        <InfoTip className="mt-0.5" label="About Aegis Governance">
          Multi-tenant governance made visible — every client, its members, the caps that bound
          their spend, and the usage measured against them, in one place.
        </InfoTip>
      </header>

      <GovernanceOverview token={token} />

      <Tabs defaultValue="tenants" className="gap-4">
        <TabsList className="w-full max-w-md">
          <TabsTrigger value="tenants">
            <Building2 className="size-4" /> Tenants
          </TabsTrigger>
          <TabsTrigger value="users">
            <Users className="size-4" /> Users
          </TabsTrigger>
          <TabsTrigger value="budgets">
            <Wallet className="size-4" /> Budgets
          </TabsTrigger>
          <TabsTrigger value="usage">
            <BarChart3 className="size-4" /> Usage
          </TabsTrigger>
        </TabsList>

        <TabsContent value="tenants" className="animate-trace-in">
          <TenantsView token={token} />
        </TabsContent>
        <TabsContent value="users" className="animate-trace-in">
          <UsersView token={token} />
        </TabsContent>
        <TabsContent value="budgets" className="animate-trace-in">
          <BudgetsView token={token} />
        </TabsContent>
        <TabsContent value="usage" className="animate-trace-in">
          <UsageView token={token} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
