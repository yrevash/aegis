'use client'

import type { ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'

import { TextSizeChoice } from './TextSizeChoice'

/**
 * Text size, on the Settings screen.
 *
 * It sits above the catalogue rather than inside it, because it is not a catalogue key:
 * every control `SettingsForm` draws is a value the *backend* resolves across platform,
 * tenant and user, and this one is a property of the browser in front of you. Putting it
 * in the generated form would mean inventing a key the server does not have, which is
 * the "hand-written second catalogue" that file exists to prevent.
 *
 * The same control is in the top bar, on every screen (see `TextSizeMenu`) — the person
 * who needs it should not have to read their way to Settings to find it.
 */
export function TextSizeCard(): ReactElement {
  return (
    <Card>
      <CardHeader
        eyebrow="accessibility · this browser"
        title={
          <span className="flex items-center gap-1">
            Text size
            <InfoTip label="What text size changes">
              Scales every word in the console — headings, tables, figures and badges —
              by setting the document&rsquo;s root font size. It is stored in this
              browser and applied before the page paints, so it survives a reload without
              a flash of the old size. It is also in the top bar of every screen.
            </InfoTip>
          </span>
        }
      />
      <CardBody className="pt-0">
        <TextSizeChoice className="max-w-sm" />
      </CardBody>
    </Card>
  )
}
