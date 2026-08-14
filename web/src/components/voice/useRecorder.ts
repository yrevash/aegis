'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

/** What the recorder is doing right now. */
export type RecorderState = 'idle' | 'requesting' | 'recording' | 'error'

/** The public surface of {@link useRecorder}. */
export interface Recorder {
  state: RecorderState
  /** Live loudness envelope while recording — one 0..1 value per animation tick. */
  levels: number[]
  /** Seconds elapsed in the current recording. */
  elapsed: number
  /** Why recording is unavailable or failed (null when fine). */
  error: string | null
  /** Whether this browser exposes the APIs at all (checked, never assumed). */
  supported: boolean
  start: () => Promise<void>
  /** Stop and resolve the recorded blob (null if nothing was captured). */
  stop: () => Promise<Blob | null>
}

/** How many envelope samples to keep — enough for a dense live waveform. */
const MAX_LEVELS = 240

/**
 * Microphone capture with a live loudness envelope.
 *
 * `MediaRecorder` produces the bytes; a parallel `AnalyserNode` on the same stream
 * produces the envelope the waveform draws, because `MediaRecorder` exposes no
 * amplitude of its own. Both are torn down together — a live microphone left open
 * after a component unmounts is a privacy bug, not a leak of memory only.
 *
 * Nothing here degrades quietly: a browser without `mediaDevices` or
 * `MediaRecorder`, or a denied permission, sets `error` and leaves `state` at
 * `error`, so the UI can say what is wrong instead of appearing to record.
 */
export function useRecorder(): Recorder {
  const [state, setState] = useState<RecorderState>('idle')
  const [levels, setLevels] = useState<number[]>([])
  const [elapsed, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [supported, setSupported] = useState(true)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const ctxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const startedAtRef = useRef(0)

  useEffect(() => {
    setSupported(
      typeof window !== 'undefined' &&
        typeof window.MediaRecorder !== 'undefined' &&
        typeof navigator !== 'undefined' &&
        Boolean(navigator.mediaDevices?.getUserMedia),
    )
  }, [])

  const teardown = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    void ctxRef.current?.close()
    ctxRef.current = null
    recorderRef.current = null
  }, [])

  // Release the microphone if the section is navigated away from mid-recording.
  useEffect(() => teardown, [teardown])

  const start = useCallback(async (): Promise<void> => {
    setError(null)
    setLevels([])
    setElapsed(0)
    setState('requesting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []

      const recorder = new MediaRecorder(stream)
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.start()
      recorderRef.current = recorder

      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (Ctor) {
        const ctx = new Ctor()
        ctxRef.current = ctx
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 1024
        ctx.createMediaStreamSource(stream).connect(analyser)
        const buf = new Uint8Array(analyser.fftSize)
        const tick = (): void => {
          analyser.getByteTimeDomainData(buf)
          let sum = 0
          for (let i = 0; i < buf.length; i += 1) {
            const centred = (buf[i] - 128) / 128
            sum += centred * centred
          }
          // RMS, lifted onto a readable scale — a speaking voice sits near 0.05
          // RMS, which would otherwise draw as an almost invisible bar.
          const rms = Math.min(1, Math.sqrt(sum / buf.length) * 3)
          setLevels((prev) => [...prev.slice(-(MAX_LEVELS - 1)), rms])
          setElapsed((Date.now() - startedAtRef.current) / 1000)
          rafRef.current = requestAnimationFrame(tick)
        }
        startedAtRef.current = Date.now()
        rafRef.current = requestAnimationFrame(tick)
      }
      setState('recording')
    } catch (err) {
      teardown()
      setState('error')
      setError(
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? 'Microphone permission was denied. Grant it in the browser and try again, or upload a file instead.'
          : `Could not start recording: ${err instanceof Error ? err.message : String(err)}`,
      )
    }
  }, [teardown])

  const stop = useCallback(async (): Promise<Blob | null> => {
    const recorder = recorderRef.current
    if (!recorder) return null
    const blob = await new Promise<Blob | null>((resolve) => {
      recorder.onstop = () => {
        resolve(
          chunksRef.current.length > 0
            ? new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
            : null,
        )
      }
      recorder.stop()
    })
    teardown()
    setState('idle')
    return blob
  }, [teardown])

  return { state, levels, elapsed, error, supported, start, stop }
}
