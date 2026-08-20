'use client'

import { AlertTriangle, BookOpen, CircleCheck, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { AccessDrawer } from '@/components/admin/AccessDrawer'
import { refusalSentence } from '@/components/settings/settingsCatalogue'
import { Badge } from '@/components/ui/Badge'
import {
  authorSkill,
  getSkills,
  setSkillActive,
  type Skill,
  type SkillScope,
  type SkillsResponse,
} from '@/lib/api/skills'
import { cn } from '@/lib/utils'

import { NEW_SKILL_TEMPLATE } from './skillDraft'

/**
 * Skills, inside the console — write one where the run that taught it is still on screen.
 *
 * Authoring lived on the settings screen, which is the right home for the full
 * three-layer management table and the wrong place to be standing when a run has just
 * gone well. The material for a skill *is* the run: the question, the tools it called,
 * the sources it stood on. Asking someone to navigate away and retype that from memory
 * is how a self-improvement loop stays theoretical.
 *
 * So this is the same mechanism at the moment it is wanted: one drawer over the console,
 * carrying the editor and the list of what is already in force. It writes through the
 * same `POST /v1/skills` the settings screen uses — no second endpoint, no second store —
 * and the settings screen remains the place to delete one or to read every layer at once.
 *
 * **The refusal is the server's own sentence.** A body the input rail blocks comes back
 * as a 422 naming the rail and the reason, and that sentence is the product working: it
 * is shown verbatim rather than collapsed into "could not save". A REDACT verdict is
 * reported too — the stored text is then not the typed text, and an author who is not
 * told that finds `[REDACTED_PERSON]` in their own runbook a week later.
 *
 * **Scope is the server's list, not a guess.** `GET /v1/skills` returns the layers this
 * caller may author at; a layer the server did not offer is never rendered as a choice.
 */

const SCOPE_LABEL: Record<SkillScope, string> = {
  platform: 'Platform — every tenant',
  tenant: 'Tenant — everyone here',
  user: 'Personal — only me',
}

export function SkillsDrawer({
  open,
  onClose,
  token,
  draft,
}: {
  open: boolean
  onClose: () => void
  token: string | null
  /**
   * A `SKILL.md` drafted from a finished run, or null for a skill written from the
   * blank template. Seeded into the box once per opening — never on every render, or an
   * author's edits would be overwritten by the draft they started from.
   */
  draft: string | null
}): ReactElement | null {
  const [data, setData] = useState<SkillsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [document, setDocument] = useState(NEW_SKILL_TEMPLATE)
  const [scope, setScope] = useState<SkillScope>('user')
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seeded = useRef<string | null>(null)

  const reload = useCallback(() => {
    getSkills(token)
      .then((next) => {
        setData(next)
        setError(null)
        setScope((current) =>
          next.scopes.includes(current) ? current : (next.scopes[next.scopes.length - 1] ?? current),
        )
      })
      .catch((failure: unknown) => setError(refusalSentence(failure)))
  }, [token])

  useEffect(() => {
    if (!open) {
      seeded.current = null
      return
    }
    reload()
  }, [open, reload])

  // Seed the box once per opening: with the run's draft when there is one, with the
  // blank template when there is not.
  useEffect(() => {
    if (!open) return
    const next = draft ?? NEW_SKILL_TEMPLATE
    if (seeded.current === next) return
    seeded.current = next
    setDocument(next)
    setRefusal(null)
    setNotice(null)
  }, [open, draft])

  const save = useCallback(async () => {
    setBusy(true)
    setRefusal(null)
    setNotice(null)
    try {
      const result = await authorSkill(token, { document, scope })
      setNotice(
        result.redactions.length > 0
          ? `Saved as ${result.row.name}. The rail masked ${result.redactions.join(', ')} before storage, so the stored text is not exactly what you typed.`
          : `Saved as ${result.row.name} and switched on. The agent can load it from the next question.`,
      )
      reload()
    } catch (failure: unknown) {
      setRefusal(refusalSentence(failure))
    } finally {
      setBusy(false)
    }
  }, [token, document, scope, reload])

  const toggle = useCallback(
    async (skill: Skill) => {
      setRefusal(null)
      try {
        await setSkillActive(token, skill.scope, skill.name, !skill.inForce)
        reload()
      } catch (failure: unknown) {
        setRefusal(refusalSentence(failure))
      }
    },
    [token, reload],
  )

  const scopes = data?.scopes ?? []
  const inForce = data?.rows.filter((row) => row.inForce).length ?? 0

  return (
    <AccessDrawer
      open={open}
      onClose={onClose}
      title={draft === null ? 'Skills' : 'Save this run as a skill'}
      subtitle={
        draft === null
          ? 'An instruction sheet the agent loads on demand, as a visible load_skill tool call.'
          : 'Drafted from what this run actually did. Edit it into what should happen every time.'
      }
    >
      <div className="flex flex-col gap-5">
        <section className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <p className="eyebrow">Write one · SKILL.md</p>
            {data !== null && (
              <Badge tone="agent" className="ml-auto">
                <BookOpen aria-hidden className="size-3" />
                {inForce} in force
              </Badge>
            )}
          </div>

          <label
            className="text-[0.78rem] font-medium text-foreground"
            htmlFor="console-skill-scope"
          >
            Who it applies to
          </label>
          <select
            id="console-skill-scope"
            value={scope}
            disabled={scopes.length === 0}
            onChange={(event) => setScope(event.target.value as SkillScope)}
            className="w-full max-w-sm rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          >
            {scopes.map((option) => (
              <option key={option} value={option}>
                {SCOPE_LABEL[option]}
              </option>
            ))}
          </select>

          <label
            className="mt-1 text-[0.78rem] font-medium text-foreground"
            htmlFor="console-skill-document"
          >
            The skill
          </label>
          <textarea
            id="console-skill-document"
            value={document}
            spellCheck={false}
            onChange={(event) => setDocument(event.target.value)}
            rows={16}
            className="w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-[0.78rem] leading-relaxed text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <p className="text-[0.74rem] leading-snug text-muted-foreground">
            Screened when you save, not when it is used: a body the guardrails refuse is
            never stored. Only the name and the description reach a prompt — the agent
            loads the rest with a <span className="font-mono">load_skill</span> tool call
            you can watch in the trace.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy || scopes.length === 0 || document.trim() === ''}
              onClick={() => void save()}
              className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Save skill'}
            </button>
            {scopes.length === 0 && data !== null && (
              <span className="text-[0.74rem] text-muted-foreground">
                This account has no layer to write a skill at.
              </span>
            )}
          </div>

          {refusal !== null && (
            <p className="flex items-start gap-2 text-sm text-block-ink">
              <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>Refused · {refusal}</span>
            </p>
          )}
          {notice !== null && (
            <p className="flex items-start gap-2 text-sm text-muted-foreground">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-ok-ink" />
              <span>{notice}</span>
            </p>
          )}
        </section>

        <section className="flex flex-col gap-2 border-t border-border pt-4">
          <p className="eyebrow">In force for this account</p>
          {error !== null ? (
            <p className="text-sm text-block-ink">{error}</p>
          ) : data === null ? (
            <p className="text-sm text-muted-foreground">Reading your skills…</p>
          ) : data.rows.length === 0 ? (
            <p className="rounded-md border border-border bg-surface-2/50 p-3 text-sm text-muted-foreground">
              No skill has been written yet. The first one applies from the next question.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {data.rows.map((skill) => (
                <li
                  key={`${skill.scope}:${skill.name}`}
                  className="flex flex-wrap items-start gap-2 rounded-md border border-border bg-surface-2/40 px-3 py-2"
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-[0.78rem] text-foreground">{skill.name}</p>
                    <p className="mt-0.5 text-[0.74rem] leading-snug text-muted-foreground">
                      {skill.description}
                    </p>
                    <p className="mt-1 text-[0.7rem] text-muted-foreground">
                      {SCOPE_LABEL[skill.scope]}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {skill.isSafety ? (
                      <Badge tone="risk">
                        <ShieldCheck aria-hidden className="size-3" />
                        Safety · always on
                      </Badge>
                    ) : skill.inForce ? (
                      <Badge tone="ok">
                        <CircleCheck aria-hidden className="size-3" />
                        In force
                      </Badge>
                    ) : (
                      <Badge tone="neutral">Off</Badge>
                    )}
                    {/* A safety skill has no control beside it: there is no value a
                        tenant could send that would switch it off, and a disabled
                        toggle would imply there was. */}
                    {!skill.isSafety && scopes.includes(skill.scope) && (
                      <button
                        type="button"
                        onClick={() => void toggle(skill)}
                        className={cn(
                          'rounded-md border border-border px-2 py-1 text-[0.74rem] text-foreground',
                          'outline-none hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring',
                        )}
                      >
                        {skill.inForce ? 'Switch off' : 'Switch on'}
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </AccessDrawer>
  )
}
