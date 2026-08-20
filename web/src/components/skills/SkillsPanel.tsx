'use client'

import { AlertTriangle, BookOpen, CircleCheck, ShieldCheck, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { refusalSentence } from '@/components/settings/settingsCatalogue'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import {
  authorSkill,
  deleteSkill,
  getSkills,
  setSkillActive,
  type Skill,
  type SkillScope,
  type SkillsResponse,
} from '@/lib/api/skills'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Skills — write one, switch it on, and see which layer decided it (§10.1–10.3).
 *
 * The autonomy surface. A platform admin writes the floor, a tenant admin writes the
 * house style, and a person writes their own — three layers of one mechanism, resolved
 * `platform ∪ tenant ∪ user` by the same settings resolver every other per-tenant
 * control goes through. The scope selector only offers the layers the server said this
 * caller may author at, and that is a courtesy: the refusal is on the server.
 *
 * **The safety skill is shown, and is not editable from here.** A platform safety skill
 * appears in every tenant's list because it applies to every tenant, marked, with no
 * control beside it — there is no value a tenant could send that would switch it off,
 * and rendering a disabled toggle would imply there was.
 *
 * **A refusal renders the server's own sentence.** When the input rail blocks an
 * authored body the reply says which rail refused it and why; that sentence is the
 * product working, so it is shown verbatim rather than collapsed into "could not save".
 */

const SCOPE_LABEL: Record<SkillScope, string> = {
  platform: 'Platform — every tenant',
  tenant: 'Tenant — everyone here',
  user: 'Personal — only me',
}

const STARTER = `---
name: my_skill
description: One sentence saying when this applies. It sits in every prompt.
triggers: [refund, invoice]
---

# What to do

- The steps, in the order you want them followed.
`

export function SkillsPanel(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [data, setData] = useState<SkillsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [document, setDocument] = useState(STARTER)
  const [scope, setScope] = useState<SkillScope>('user')
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(() => {
    if (!hydrated) return
    getSkills(token)
      .then((next) => {
        setData(next)
        setError(null)
        setScope((current) =>
          next.scopes.includes(current) ? current : (next.scopes[next.scopes.length - 1] ?? current),
        )
      })
      .catch((failure: unknown) => setError(refusalSentence(failure)))
  }, [token, hydrated])

  useEffect(reload, [reload])

  const save = useCallback(async () => {
    setBusy(true)
    setRefusal(null)
    setNotice(null)
    try {
      const result = await authorSkill(token, { document, scope })
      setNotice(
        result.redactions.length > 0
          ? `Saved as ${result.row.name}. The rail masked ${result.redactions.join(', ')} before storage, so the stored text is not exactly what you typed.`
          : `Saved as ${result.row.name} and switched on.`,
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

  const remove = useCallback(
    async (skill: Skill) => {
      setRefusal(null)
      try {
        await deleteSkill(token, skill.scope, skill.name)
        reload()
      } catch (failure: unknown) {
        setRefusal(refusalSentence(failure))
      }
    },
    [token, reload],
  )

  const scopes = data?.scopes ?? []

  return (
    <Card>
      <CardHeader
        title="Skills"
        eyebrow="platform ∪ tenant ∪ you · the body loads on demand, as a visible tool call"
        actions={
          data === null ? null : (
            <Badge tone="agent">
              <BookOpen aria-hidden className="size-3" />
              {data.rows.filter((row) => row.inForce).length} in force
            </Badge>
          )
        }
      />
      <CardBody>
        {error !== null ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : data === null ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Reading your skills…</p>
        ) : (
          <>
            {data.rows.length === 0 ? (
              <p className="mb-4 rounded-md border border-border bg-surface-2 p-4 text-sm text-muted-foreground">
                No skill has been written yet. A skill is a short instruction sheet the agent
                can read when it decides it needs one — how your team closes a request, what
                your refund window is, how you like an answer laid out. Write one below and it
                applies from the next question.
              </p>
            ) : (
              <div className="mb-6 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                      <th className="pb-2 pr-4 font-medium">Skill</th>
                      <th className="pb-2 pr-4 font-medium">Layer</th>
                      <th className="pb-2 pr-4 font-medium">Status</th>
                      <th className="pb-2 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((skill) => (
                      <tr
                        key={`${skill.scope}:${skill.name}`}
                        className="border-t border-border align-top"
                      >
                        <td className="py-3 pr-4">
                          <p className="font-mono text-[0.78rem] text-foreground">{skill.name}</p>
                          <p className="mt-1 max-w-lg text-[0.74rem] leading-snug text-muted-foreground">
                            {skill.description}
                          </p>
                        </td>
                        <td className="py-3 pr-4 text-[0.78rem] text-muted-foreground">
                          {SCOPE_LABEL[skill.scope]}
                        </td>
                        <td className="py-3 pr-4">
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
                        </td>
                        <td className="py-3">
                          {skill.isSafety || !scopes.includes(skill.scope) ? (
                            <span className="text-[0.74rem] text-muted-foreground">
                              {skill.isSafety
                                ? 'Set by the platform'
                                : 'Not yours to change'}
                            </span>
                          ) : (
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => void toggle(skill)}
                                className="rounded-md border border-border px-2 py-1 text-[0.74rem] text-foreground hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              >
                                {skill.inForce ? 'Switch off' : 'Switch on'}
                              </button>
                              <button
                                type="button"
                                aria-label={`Delete ${skill.name}`}
                                onClick={() => void remove(skill)}
                                className="rounded-md border border-border px-2 py-1 text-[0.74rem] text-foreground hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                              >
                                <Trash2 aria-hidden className="size-3.5" />
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="border-t border-border pt-5">
              <p className="eyebrow mb-2">write one · SKILL.md format</p>
              <label className="mb-2 block text-[0.78rem] font-medium text-foreground" htmlFor="skill-scope">
                Who it applies to
              </label>
              <select
                id="skill-scope"
                value={scope}
                onChange={(event) => setScope(event.target.value as SkillScope)}
                className="mb-3 w-full max-w-sm rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                {scopes.map((option) => (
                  <option key={option} value={option}>
                    {SCOPE_LABEL[option]}
                  </option>
                ))}
              </select>
              <label className="mb-2 block text-[0.78rem] font-medium text-foreground" htmlFor="skill-document">
                The skill
              </label>
              <textarea
                id="skill-document"
                value={document}
                spellCheck={false}
                onChange={(event) => setDocument(event.target.value)}
                rows={12}
                className="w-full rounded-md border border-border bg-card px-3 py-2 font-mono text-[0.78rem] leading-relaxed text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              />
              <p className="mt-2 text-[0.74rem] text-muted-foreground">
                Screened when you save, not when it is used: a body the guardrails refuse is
                never stored. Only the name and the description reach a prompt — the agent
                loads the rest with a <span className="font-mono">load_skill</span> tool call
                you can watch in the trace.
              </p>
              <div className="mt-3 flex items-center gap-3">
                <button
                  type="button"
                  disabled={busy || scopes.length === 0}
                  onClick={() => void save()}
                  className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
                >
                  {busy ? 'Saving…' : 'Save skill'}
                </button>
                {scopes.length === 0 ? (
                  <span className="text-[0.74rem] text-muted-foreground">
                    This account has no layer to write a skill at.
                  </span>
                ) : null}
              </div>
              {refusal !== null ? (
                <p className="mt-3 flex items-start gap-2 text-sm text-danger">
                  <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
                  <span>Refused · {refusal}</span>
                </p>
              ) : null}
              {notice !== null ? (
                <p className="mt-3 flex items-start gap-2 text-sm text-muted-foreground">
                  <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-ok" />
                  <span>{notice}</span>
                </p>
              ) : null}
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}
