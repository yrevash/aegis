import { StatCard } from '@/components/ui/StatCard'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import type { Section } from '@/lib/portal'

/**
 * SectionPlaceholder — a titled empty dashboard for any not-yet-wired section, so
 * navigation works end-to-end. Shows the section's honest tooltip and a couple of
 * neutral placeholder tiles + a panel, all on the real design system.
 */
export function SectionPlaceholder({ section }: { section: Section }) {
  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">{section.hint}</p>
        <h1 className="t-hero text-foreground">{section.label}</h1>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <StatCard label="Placeholder metric" value="—" icon={section.icon} tone="graph" />
        <StatCard label="Placeholder metric" value="—" icon={section.icon} tone="agent" />
        <StatCard label="Placeholder metric" value="—" icon={section.icon} tone="ml" />
      </div>

      <Card>
        <CardHeader
          eyebrow="not yet wired"
          title={`${section.label} panel`}
          description="This surface is a placeholder in the scaffold. It will be wired to the typed API client in a later task."
        />
        <CardBody>
          <div className="flex min-h-[200px] items-center justify-center rounded-xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
            Content renders here once wired
          </div>
        </CardBody>
      </Card>
    </div>
  )
}
