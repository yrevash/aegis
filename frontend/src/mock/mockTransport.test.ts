/**
 * Unit tests for the pure {@link buildRunScript} — the persona branch that
 * fixes "multi-user is cosmetic".
 *
 * We assert on the scripted event bodies (no timers, fully deterministic):
 * the `client` persona is own-scope and gets its action tool denied without a
 * human gate; an admin/`operations_lead` persona reaches the human gate and,
 * once approved, executes the action.
 */

import { describe, expect, it } from 'vitest'

import type { StreamEventBody } from '@/types/stream'

import { buildRunScript } from './mockTransport'

/** Flatten a step list to its event bodies. */
const bodies = (steps: { body: StreamEventBody }[]): StreamEventBody[] => steps.map((s) => s.body)

describe('buildRunScript — client (customer) persona', () => {
  const script = buildRunScript('client')
  const lead = bodies(script.lead)

  it('never pauses at the human gate', () => {
    expect(script.gated).toBe(false)
    expect(lead.some((e) => e.type === 'approval_required')).toBe(false)
  })

  it('denies the action tool (not allowlisted)', () => {
    const result = lead.find((e) => e.type === 'tool_result')
    expect(result).toBeDefined()
    expect(result?.type === 'tool_result' && result.ok).toBe(false)
    expect(result?.type === 'tool_result' && result.summary).toMatch(/denied/i)
  })

  it('scores with a confident, NON-gated ML explanation before the action', () => {
    const ml = lead.find((e) => e.type === 'ml_explanation')
    expect(ml?.type === 'ml_explanation' && ml.gated).toBe(false)
    // The ML node fires before the denied tool call so the flow-map `ml` lights.
    const mlIdx = lead.findIndex((e) => e.type === 'ml_explanation')
    const toolIdx = lead.findIndex((e) => e.type === 'tool_call')
    expect(mlIdx).toBeGreaterThanOrEqual(0)
    expect(mlIdx).toBeLessThan(toolIdx)
  })

  it('emits the `ml` node (not the legacy `score` id)', () => {
    const started = lead.find((e) => e.type === 'node_started' && e.node === 'ml')
    expect(started).toBeDefined()
    expect(lead.some((e) => e.type === 'node_started' && e.node === 'score')).toBe(false)
  })

  it('is own-scope: a small reranked candidate set', () => {
    const reranked = lead.find((e) => e.type === 'retrieval' && e.status === 'reranked')
    expect(reranked?.type === 'retrieval' && reranked.scored_sources.length).toBe(3)
  })

  it('still redacts PII on the input rail', () => {
    const redact = lead.find((e) => e.type === 'guardrail' && e.verdict === 'redact')
    expect(redact?.type === 'guardrail' && redact.redactions.map((r) => r.kind)).toEqual(['ssn', 'email'])
    expect(redact?.type === 'guardrail' && redact.after).toContain('[REDACTED_SSN]')
  })

  it('completes the run', () => {
    const finished = lead.find((e) => e.type === 'run_finished')
    expect(finished?.type === 'run_finished' && finished.status).toBe('completed')
  })
})

describe('buildRunScript — operations_lead (admin) persona', () => {
  const script = buildRunScript('operations_lead')
  const lead = bodies(script.lead)

  it('pauses at the human gate with a high-risk action', () => {
    expect(script.gated).toBe(true)
    const gate = lead.at(-1)
    expect(gate?.type).toBe('approval_required')
    expect(gate?.type === 'approval_required' && gate.risk).toBe('high')
  })

  it('does not resolve the tool before the gate', () => {
    expect(lead.some((e) => e.type === 'tool_result')).toBe(false)
  })

  it('scores with a gated ML explanation on the `ml` node', () => {
    const ml = lead.find((e) => e.type === 'ml_explanation')
    expect(ml?.type === 'ml_explanation' && ml.gated).toBe(true)
    expect(ml?.type === 'ml_explanation' && ml.min_confidence).toBe(0.85)
    // Backend node id is `ml`, not the legacy `score`.
    expect(lead.some((e) => e.type === 'node_started' && e.node === 'ml')).toBe(true)
    expect(lead.some((e) => e.type === 'node_started' && e.node === 'score')).toBe(false)
  })

  it('reranks a full candidate set with scores', () => {
    const reranked = lead.find((e) => e.type === 'retrieval' && e.status === 'reranked')
    expect(reranked?.type === 'retrieval' && reranked.scored_sources.length).toBe(5)
  })

  it('executes the action on approval', () => {
    const tail = bodies(script.tail('approve'))
    const result = tail.find((e) => e.type === 'tool_result')
    expect(result?.type === 'tool_result' && result.ok).toBe(true)
    const finished = tail.find((e) => e.type === 'run_finished')
    expect(finished?.type === 'run_finished' && finished.status).toBe('completed')
  })

  it('blocks the run on rejection', () => {
    const tail = bodies(script.tail('reject'))
    const result = tail.find((e) => e.type === 'tool_result')
    expect(result?.type === 'tool_result' && result.ok).toBe(false)
    const finished = tail.find((e) => e.type === 'run_finished')
    expect(finished?.type === 'run_finished' && finished.status).toBe('blocked')
  })
})

describe('buildRunScript — persona routing', () => {
  it('treats "customer" as a client persona', () => {
    expect(buildRunScript('customer').gated).toBe(false)
  })

  it('defaults an unknown or null persona to the full admin trajectory', () => {
    expect(buildRunScript(null).gated).toBe(true)
    expect(buildRunScript('platform-admin').gated).toBe(true)
  })
})
