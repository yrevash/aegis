'use client'

import { useCallback, useState, type ReactElement } from 'react'

import { SettingsForm } from '@/components/settings/SettingsForm'
import { ToolRosterCard } from '@/components/settings/ToolRosterCard'
import { BackendGate } from '@/components/shared/BackendGate'

/**
 * Settings — the per-tenant control plane, and the one screen that says *who decided*.
 *
 * A mount, not a form. Everything below is generated from the settings catalogue by
 * `SettingsForm`: there is no list of keys on this screen, so a control added to
 * `aegis.settings.spec.SETTING_SPECS` appears here with nothing in `web/` edited. That
 * is the mechanism behind "operating this platform never requires touching code" — the
 * first bespoke settings form is the moment the claim stops being true.
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
      <div>
        <p className="eyebrow mb-1">platform → tenant → you · every value names who decided</p>
        <h1 className="t-hero text-foreground">Settings</h1>
      </div>

      <SettingsForm onWritten={bump} />
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
