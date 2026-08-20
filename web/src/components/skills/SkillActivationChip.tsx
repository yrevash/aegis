'use client'

import { BookOpen } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'

/**
 * The activation chip — "the agent decided it needed a skill, and here it is" (§10.3).
 *
 * Progressive disclosure makes a skill load a **real tool call**, which is the whole
 * reason this chip can exist: there is a `tool_call` event in the stream, with a name,
 * an argument and a risk tier, exactly like every other action. Before §10.2 a skill
 * was pasted into the prompt upstream of everything, and a turn that used one was
 * indistinguishable in the trace from a turn that did not — so "see how self-improving
 * prompts help them" was an assertion with no artefact behind it.
 *
 * Rendered as a chip rather than a row of its own because it is an annotation on a tool
 * call the trace is already showing: a second entry would double-count the event.
 */
export function SkillActivationChip({ name }: { name: string }): ReactElement {
  return (
    <Badge tone="agent">
      <BookOpen aria-hidden className="size-3" />
      Skill loaded · <span className="font-mono">{name}</span>
    </Badge>
  )
}
