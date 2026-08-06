/**
 * Tests for the live-first auto-probe fallback logic. {@link decideMode} is the
 * pure decision table; {@link probeBackend} is exercised with an injected fetch
 * so no network is touched.
 */

import { describe, expect, it } from 'vitest'

import { decideMode, probeBackend } from './mode'

describe('decideMode — pure fallback resolution', () => {
  it('forces mock when forceMock is set, regardless of probe outcome', () => {
    expect(decideMode(true, true)).toEqual({ mode: 'mock', reason: 'forced-mock' })
    expect(decideMode(true, false)).toEqual({ mode: 'mock', reason: 'forced-mock' })
    expect(decideMode(true, null)).toEqual({ mode: 'mock', reason: 'forced-mock' })
  })

  it('falls back to labelled mock when the probe fails', () => {
    expect(decideMode(false, false)).toEqual({ mode: 'mock', reason: 'probe-failed' })
  })

  it('goes live when the backend is reachable', () => {
    expect(decideMode(false, true)).toEqual({ mode: 'live', reason: 'probe-live' })
  })

  it('defaults optimistically to live before the probe resolves', () => {
    expect(decideMode(false, null)).toEqual({ mode: 'live', reason: 'probe-live' })
  })
})

describe('probeBackend — with an injected fetch', () => {
  it('resolves live when the health endpoint answers ok', async () => {
    const fetchImpl = (async () => new Response(null, { status: 200 })) as unknown as typeof fetch
    expect(await probeBackend({ fetchImpl })).toEqual({ mode: 'live', reason: 'probe-live' })
  })

  it('falls back to mock on a network error', async () => {
    const fetchImpl = (async () => {
      throw new Error('connection refused')
    }) as unknown as typeof fetch
    expect(await probeBackend({ fetchImpl })).toEqual({ mode: 'mock', reason: 'probe-failed' })
  })

  it('falls back to mock on a non-ok status', async () => {
    const fetchImpl = (async () => new Response(null, { status: 503 })) as unknown as typeof fetch
    expect(await probeBackend({ fetchImpl })).toEqual({ mode: 'mock', reason: 'probe-failed' })
  })
})
