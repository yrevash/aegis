'use client'

import { useCallback, useState, type ReactElement } from 'react'

import { PageHeader } from '@/components/primitives/PageHeader'
import { SettingsForm } from '@/components/settings/SettingsForm'
import { TextSizeCard } from '@/components/settings/TextSizeCard'
import { ToolRosterCard } from '@/components/settings/ToolRosterCard'
import { BackendGate } from '@/components/shared/BackendGate'
import { SkillsPanel } from '@/components/skills/SkillsPanel'

/**
 * Settings — the per-tenant control plane, and the one screen that says *who decided*.
 *
 * A mount, not a form. Everything below is generated from the settings catalogue by
 * `SettingsForm`: there is no list of keys on this screen, so a control added to
 * `aegis.settings.spec.SETTING_SPECS` appears here with nothing in `web/` edited. That
 * is the mechanism behind "operating this platform never requires touching code" — the
 * first bespoke settings form is the moment the claim stops being true.
 *
 * Skills sit on this screen for the same reason the roster does: which skills are in
 * force is the value of one catalogue key (`skills.enabled`, resolved
 * `platform ∪ tenant ∪ user`), so a skill is a per-tenant control like every other one
 * — and it is the control this screen exists for that a *person* can write, not only an
 * administrator. Every role's portal reaches this section, which is what makes "the
 * user has autonomy over how the agent works for them" reachable rather than asserted.
 *
 * The tool roster sits under it because it is a **projection of those same settings**:
 * the gate floor it prints is `agent.gate_min_risk`, resolved. It re-reads on every
 * accepted write, because a write the server took and a panel that still shows the old
 * floor is, to whoever is looking, the same thing as the control being broken.
 */
function SettingsView(): ReactElement {
  const [written, setWritten] = useState(0)
  const bump = useCallback(() => setWritten((n) => n + 1), [])

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="platform → tenant → you · every value names who decided"
        title="Settings"
      />

      {/* Above the catalogue on purpose: it is the one control on this screen that
          changes nothing on the server and everything about whether the rest of the
          screen can be read. It is also in the top bar of every page. */}
      <TextSizeCard />

      <SettingsForm onWritten={bump} />
      <SkillsPanel />
      <ToolRosterCard refreshKey={written} />
    </div>
  )
}

/** Client entry for the Settings section — gated on a reachable backend. */
export function SettingsMount(): ReactElement {
  return (
    <BackendGate>
      <SettingsView />
    </BackendGate>
  )
}
