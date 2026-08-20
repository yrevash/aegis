'use client'

import {
  CircleStop,
  Loader2,
  Mic,
  SendHorizontal,
  Upload,
} from 'lucide-react'
import {
  useCallback,
  useRef,
  useState,
  type ChangeEvent,
  type ReactElement,
} from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { Button } from '@/components/primitives/button'
import { PageHeader } from '@/components/primitives/PageHeader'
import { BackendGate } from '@/components/shared/BackendGate'
import { RailVerdict } from '@/components/voice/RailVerdict'
import { TranscriptPanel } from '@/components/voice/TranscriptPanel'
import { Waveform } from '@/components/voice/Waveform'
import { useRecorder } from '@/components/voice/useRecorder'
import { formatSeconds, toWav } from '@/components/voice/wav'
import { transcribeVoice } from '@/lib/api/client'
import { startRun } from '@/lib/api/liveTransport'
import type { VoiceTranscribeResponse } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'
import type { RunStatus } from '@/lib/stream'

/** What the agent hand-off produced, once one has been run. */
interface AgentRun {
  answer: string
  status: RunStatus | 'running' | 'error'
  error: string | null
}

/**
 * Aegis Voice — record or upload speech, transcribe it, and see the rails judge
 * the transcript before a word of it can reach the agent.
 *
 * The screen is arranged to make the security ordering legible, because the
 * ordering *is* the feature: capture → transcript (evidence) → rail verdict →
 * and only then a hand-off action that is disabled unless the rails cleared it.
 * The button sends `agent_input`, never `transcript`; forwarding the raw
 * transcript would defeat the rails, so the field the UI sends is the one the
 * rails returned.
 */
function VoiceView(): ReactElement {
  const { session } = useAuth()
  const token = session?.token ?? null
  const recorder = useRecorder()
  const fileRef = useRef<HTMLInputElement>(null)

  const [peaks, setPeaks] = useState<number[]>([])
  const [duration, setDuration] = useState<number | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [result, setResult] = useState<VoiceTranscribeResponse | null>(null)
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null)

  const submit = useCallback(
    async (blob: Blob, label: string): Promise<void> => {
      setBusy(true)
      setNotice(null)
      setResult(null)
      setAgentRun(null)
      setSource(label)
      try {
        // Re-encoded to PCM WAV in the browser: the accepted container list is
        // wav/mp3/ogg/flac/m4a (MediaRecorder emits WebM), and the server's
        // silence-splitter reads WAV only — fleet-only policy means no ffmpeg.
        const prepared = await toWav(blob)
        setPeaks(prepared?.peaks ?? [])
        setDuration(prepared?.duration ?? null)
        if (!prepared) {
          setNotice(
            'This browser could not decode the recording, so it was uploaded in its original container. Long-audio chunking needs PCM WAV and will not apply.',
          )
        }
        const upload = prepared?.wav ?? blob
        const filename = prepared ? 'recording.wav' : (blob as File).name || 'recording'
        setResult(await transcribeVoice(upload, { filename }, token))
      } catch (err) {
        // `err.message` is a whole sentence now (see `lib/api/apiError.ts`), so this
        // joins two sentences rather than gluing a status line onto a suffix.
        const said = err instanceof Error ? err.message : String(err)
        setNotice(`The recording was not transcribed. ${said} Nothing was sent to the agent.`)
      } finally {
        setBusy(false)
      }
    },
    [token],
  )

  const onStop = useCallback(async (): Promise<void> => {
    const blob = await recorder.stop()
    if (!blob) {
      setNotice('Recording produced no audio.')
      return
    }
    await submit(blob, 'microphone')
  }, [recorder, submit])

  const onFile = useCallback(
    async (event: ChangeEvent<HTMLInputElement>): Promise<void> => {
      const file = event.target.files?.[0]
      event.target.value = ''
      if (file) await submit(file, file.name)
    },
    [submit],
  )

  const sendToAgent = useCallback((): void => {
    const input = result?.agent_input
    // Belt and braces: the button is disabled without cleared text, and the
    // handler refuses to run without it too.
    if (!input) return
    setAgentRun({ answer: '', status: 'running', error: null })
    // A voice turn is deliberately single-shot: the transcript is the input, and
    // there is no thread for it to belong to.
    startRun({ query: input, persona: null, sessionId: null }, token, {
      onEvent: (event) => {
        if (event.type === 'token') {
          setAgentRun((prev) =>
            prev ? { ...prev, answer: prev.answer + event.text } : prev,
          )
        } else if (event.type === 'run_finished') {
          setAgentRun((prev) => (prev ? { ...prev, status: event.status } : prev))
        } else if (event.type === 'error') {
          setAgentRun((prev) =>
            prev ? { ...prev, status: 'error', error: event.message } : prev,
          )
        }
      },
      onError: (error) => {
        setAgentRun((prev) => (prev ? { ...prev, status: 'error', error: error.message } : prev))
      },
      onClose: () => {
        setAgentRun((prev) =>
          prev && prev.status === 'running' ? { ...prev, status: 'completed' } : prev,
        )
      },
    })
  }, [result, token])

  const recording = recorder.state === 'recording'

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Whisper · rails"
        title="Voice"
      />

      <Card>
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {recording ? (
              <Button variant="destructive" onClick={() => void onStop()}>
                <CircleStop className="size-4" /> Stop &amp; transcribe
              </Button>
            ) : (
              <Button
                onClick={() => void recorder.start()}
                disabled={busy || !recorder.supported || recorder.state === 'requesting'}
              >
                <Mic className="size-4" /> Record
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={busy || recording}
            >
              <Upload className="size-4" /> Upload a file
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => void onFile(e)}
            />
            {recording && (
              <span className="font-mono text-sm tabular-nums text-block-ink">
                ● {formatSeconds(recorder.elapsed)}
              </span>
            )}
            {busy && (
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Transcribing…
              </span>
            )}
            {source && !busy && (
              <Badge tone="neutral" className="ml-auto font-mono">
                {source}
              </Badge>
            )}
          </div>

          <Waveform
            values={recording ? recorder.levels : peaks}
            hex={recording ? 'var(--block)' : 'var(--blue-200)'}
            label={recording ? 'Live microphone level' : 'Recorded waveform'}
          />

          {!recorder.supported && (
            <p className="text-xs text-muted-foreground">
              This browser exposes no <code className="font-mono">MediaRecorder</code> or
              microphone API, so live recording is unavailable — upload a file instead.
            </p>
          )}
          {recorder.error && <p className="text-xs text-block-ink">{recorder.error}</p>}
          {notice && <p className="text-xs text-block-ink">{notice}</p>}
          {!recording && peaks.length > 0 && duration != null && (
            <p className="text-xs text-muted-foreground">
              {formatSeconds(duration)} captured · re-encoded to 16-bit PCM WAV in the browser so
              the accepted-container check and the server-side silence splitter both apply.
            </p>
          )}
        </CardBody>
      </Card>

      {result === null ? (
        <Card>
          <CardBody>
            <p className="text-sm text-muted-foreground">
              No transcription yet. Record a clip or upload a file — the transcript, its
              segments and the rail verdict on it appear here. Nothing is measured or shown
              until a recording has actually been transcribed.
            </p>
          </CardBody>
        </Card>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardBody>
              <div className="mb-4 flex items-center justify-between">
                <h3 className="t-title text-foreground">Transcript</h3>
              </div>
              <TranscriptPanel result={result} />
            </CardBody>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardBody>
                <RailVerdict result={result} />
              </CardBody>
            </Card>

            <Card>
              <CardBody className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="t-title text-foreground">Send to the agent</h3>
                  <Button
                    onClick={sendToAgent}
                    disabled={result.agent_input == null || agentRun?.status === 'running'}
                  >
                    <SendHorizontal className="size-4" />
                    {agentRun?.status === 'running' ? 'Running…' : 'Run'}
                  </Button>
                </div>
                {agentRun === null ? (
                  <p className="text-sm text-muted-foreground">
                    {result.agent_input == null
                      ? 'Disabled — the rails blocked this transcript, so there is nothing to send.'
                      : 'Runs the full agent pipeline on the rails-cleared text, exactly as a typed query would.'}
                  </p>
                ) : (
                  <div className="space-y-2">
                    <Badge
                      tone={
                        agentRun.status === 'error' || agentRun.status === 'blocked'
                          ? 'block'
                          : agentRun.status === 'running'
                            ? 'neutral'
                            : 'ok'
                      }
                      className="uppercase"
                    >
                      {agentRun.status}
                    </Badge>
                    <p className="rounded-lg border border-border bg-surface-2/30 px-3.5 py-3 text-sm leading-relaxed text-foreground">
                      {agentRun.error ||
                        agentRun.answer ||
                        'The run produced no answer text.'}
                    </p>
                  </div>
                )}
              </CardBody>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}

/** Client entry for the Voice section — gated on a reachable backend. */
export function VoiceMount(): ReactElement {
  return (
    <BackendGate>
        <VoiceView />
    </BackendGate>
  )
}
