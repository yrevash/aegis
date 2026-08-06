/**
 * Pure-logic tests for the Access demo decision helpers (§4.8). Runs in the
 * Node/vitest environment — no React, no recharts — by importing only the pure
 * module and the plain `RunState` shape.
 */

import { describe, expect, it } from 'vitest'

import { initialRunState, type RunState } from '@/state/runReducer'
import type { ToolCall, ToolResult } from '@/types/stream'

import { gateStatus, isSettled, toolMark } from './simLogic'

const state = (patch: Partial<RunState>): RunState => ({ ...initialRunState, ...patch })

const toolCall = (): ToolCall => ({ seq: 1, type: 'tool_call' } as unknown as ToolCall)
const toolResult = (ok: boolean): ToolResult => ({ seq: 2, type: 'tool_result', ok } as unknown as ToolResult)

describe('toolMark', () => {
  it('reports "none" before any tool activity', () => {
    expect(toolMark(state({}))).toEqual({ mark: 'none', label: '—' })
  })

  it('reports a proposal (gate) when a call was issued but no result yet', () => {
    expect(toolMark(state({ toolCalls: [toolCall()] }))).toEqual({ mark: 'gate', label: 'Refund proposed' })
  })

  it('reports "executed" (allow) on a successful result', () => {
    expect(toolMark(state({ toolCalls: [toolCall()], toolResults: [toolResult(true)] }))).toEqual({
      mark: 'allow',
      label: 'Refund executed',
    })
  })

  it('reports "denied" (deny) when any result failed', () => {
    expect(toolMark(state({ toolCalls: [toolCall()], toolResults: [toolResult(false)] }))).toEqual({
      mark: 'deny',
      label: 'Refund denied',
    })
  })
})

describe('isSettled', () => {
  it('is false before either lane starts', () => {
    expect(isSettled(state({}), state({}))).toBe(false)
  })

  it('is false while a lane is still running', () => {
    const running = state({ events: [{ seq: 0 } as never], running: true })
    expect(isSettled(running, state({}))).toBe(false)
  })

  it('is true once a started run has stopped running', () => {
    const done = state({ events: [{ seq: 0 } as never], running: false })
    expect(isSettled(done, state({}))).toBe(true)
  })
})

describe('gateStatus', () => {
  it('is idle before the gate is reached', () => {
    expect(gateStatus(state({}))).toBe('idle')
  })

  it('is pending while an approval is active', () => {
    expect(gateStatus(state({ approval: { seq: 3 } as never, awaitedApproval: true }))).toBe('pending')
  })

  it('is live after the gate fired and resolved', () => {
    expect(gateStatus(state({ approval: null, awaitedApproval: true }))).toBe('live')
  })
})
