import type { ReactElement } from 'react'

import { LandingSection } from './LandingSection'

/**
 * The trust stack, drawn as the path a single request takes.
 *
 * The stages are the real sequence in the agent graph. Earlier passes rendered
 * them as six cards or six paragraphs, which read as an unordered feature list —
 * the ordering *is* the information, so it is a numbered rail with the request
 * travelling down it, and each stage is three words.
 *
 * **The six coloured dots are gone.** They carried a hue per stage — one of them
 * `--block`, one `--risk`, one `--ok` — which spends the three reserved status
 * colours on things that are not statuses. DESIGN.md §2 is explicit that those
 * three mean guardrail, gate and healthy wherever they appear, so a "Retrieval"
 * stage tinted the same amber as "awaiting human approval" is a colour that
 * means nothing, on the one page where a reader is learning the vocabulary. The
 * stages are told apart by their number, their label and their position, which
 * is the order DESIGN.md asks for.
 *
 * The gate is still called out, because it is the claim that distinguishes this
 * from an agent framework: a run stops on a tool's risk tier, not on model
 * confidence. It is called out in words, in the sentence under the rail.
 */

const STAGES = [
  { n: '01', label: 'Input rails', note: 'injection · PII · schema' },
  { n: '02', label: 'Retrieval', note: 'cited · provenance' },
  { n: '03', label: 'Signal', note: 'conformal · SHAP' },
  { n: '04', label: 'Human gate', note: 'by tool risk tier' },
  { n: '05', label: 'Governance', note: 'budget · RLS' },
  { n: '06', label: 'Audit', note: 'OTel · append-only' },
] as const

export function TrustStack(): ReactElement {
  return (
    <LandingSection
      id="trust"
      eyebrow="Trust"
      title="Six checkpoints between the model and a real action."
      note={
        <>
          The gate fires on a tool&rsquo;s{' '}
          <strong className="font-semibold text-foreground">risk tier</strong>, never on model
          confidence. A low-risk note is written without asking; a status change is not.
        </>
      }
    >
      <ol className="grid gap-x-4 gap-y-6 sm:grid-cols-2 lg:grid-cols-6">
        {STAGES.map((stage) => (
          <li key={stage.n} className="min-w-0 border-t border-border pt-4">
            <span className="tabular block font-mono text-[0.68rem] tracking-[0.16em] text-muted-foreground">
              {stage.n}
            </span>
            <p className="mt-2 text-[0.9375rem] font-semibold tracking-[-0.01em] text-foreground">
              {stage.label}
            </p>
            <p className="mt-1 font-mono text-[0.68rem] leading-relaxed text-muted-foreground">
              {stage.note}
            </p>
          </li>
        ))}
      </ol>
    </LandingSection>
  )
}
