'use client'

import {
  CircleStop,
  Clock,
  Coins,
  Layers,
  Loader2,
  Mic,
  Receipt as ReceiptIcon,
  SendHorizontal,
  Upload,
} from 'lucide-react'
import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactElement,
} from 'react'

import { SceneState } from '@/components/illustration/Scene'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { RailVerdict } from '@/components/voice/RailVerdict'
import {
  SegmentTimeline,
  chunkBoundaries,
  timedSegments,
  timelineSpan,
} from '@/components/voice/SegmentTimeline'
import { TranscriptPanel } from '@/components/voice/TranscriptPanel'
import { Waveform } from '@/components/voice/Waveform'
import { useRecorder } from '@/components/voice/useRecorder'
import { formatSeconds, toWav } from '@/components/voice/wav'
import { transcribeVoice } from '@/lib/api/client'
import { startRun } from '@/lib/api/liveTransport'
import type { VoiceTranscribeResponse } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'
import type { RunStatus } from '@/lib/stream'

/** Built once — a formatter rebuilt per row is the expensive half of `Intl`. */
const COUNT = new Intl.NumberFormat('en-US')
const SECONDS = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 6,
  maximumFractionDigits: 6,
})
const PERCENT = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 })

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
 *
 * **`segments[]` is the one true within-clip series in this portal**, and it is
 * drawn twice, deliberately: once as a timeline you can look at, and once as the
 * table of the same rows. Per-segment *confidence* is not drawn at all — this
 * Whisper deployment reports none (`has_confidence`), so it is a stated absence
 * rather than a column of dashes or a plausible 0.94 beside every line.
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

  // The one real series: segments placed on the clip's own clock. The reported
  // duration wins; without one, the last segment's end is the measured span.
  const timeline = useMemo(() => {
    const segments = timedSegments(result?.segments ?? [])
    const span = timelineSpan(segments, result?.duration_seconds ?? duration)
    return { segments, span, untimed: (result?.segments.length ?? 0) - segments.length }
  }, [result, duration])

  // The same chunk boundaries, projected onto the capture waveform — the only
  // structure that waveform can honestly carry.
  const captureMarks = useMemo(
    () => (recording ? [] : chunkBoundaries(timeline.segments, timeline.span)),
    [recording, timeline],
  )

  return (
    <div className="min-w-0 space-y-6">
      <PageHeader eyebrow="Whisper · rails" title="Voice" />

      <p aria-live="polite" className="sr-only">
        {busy
          ? 'Transcribing the recording.'
          : result == null
            ? ''
            : `Transcribed. The rails returned ${result.verdict}.`}
      </p>

      <Card className="min-w-0">
        <CardHeader
          eyebrow="capture"
          title="Recording"
          actions={
            <InfoTip label="Why the browser re-encodes the audio">
              MediaRecorder emits WebM, which payload hygiene refuses and the server&apos;s
              silence splitter cannot read — so the clip is re-encoded to PCM WAV before it
              is sent.
            </InfoTip>
          }
        />
        <CardBody className="min-w-0 space-y-4 pt-4">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {recording ? (
              <Button variant="destructive" onClick={() => void onStop()}>
                <CircleStop className="size-4" aria-hidden /> Stop &amp; transcribe
              </Button>
            ) : (
              <Button
                onClick={() => void recorder.start()}
                disabled={busy || !recorder.supported || recorder.state === 'requesting'}
              >
                <Mic className="size-4" aria-hidden /> Record
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => fileRef.current?.click()}
              disabled={busy || recording}
            >
              <Upload className="size-4" aria-hidden /> Upload a file
            </Button>
            <label htmlFor="voice-file" className="sr-only">
              Audio file to transcribe
            </label>
            <input
              id="voice-file"
              ref={fileRef}
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => void onFile(e)}
            />
            {recording && (
              <Figure className="text-block-ink">● {formatSeconds(recorder.elapsed)}</Figure>
            )}
            {busy && (
              <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden /> Transcribing…
              </span>
            )}
            {source && !busy && (
              <Badge tone="neutral" className="ml-auto max-w-full truncate font-mono">
                {source}
              </Badge>
            )}
          </div>

          <Waveform
            values={recording ? recorder.levels : peaks}
            hex={recording ? 'var(--block)' : 'var(--blue-200)'}
            marks={captureMarks}
            label={
              recording
                ? 'Live microphone level'
                : captureMarks.length > 0
                  ? `Recorded waveform, with ${captureMarks.length} chunk boundaries marked`
                  : 'Recorded waveform'
            }
          />

          {!recorder.supported && (
            <p className="text-xs text-muted-foreground">
              This browser exposes no <code className="font-mono">MediaRecorder</code> or
              microphone API — upload a file instead.
            </p>
          )}
          {recorder.error && (
            <p role="status" className="text-xs break-words text-block-ink">
              {recorder.error}
            </p>
          )}
          {notice && (
            <p role="status" className="text-xs break-words text-block-ink">
              {notice}
            </p>
          )}
          {!recording && peaks.length > 0 && duration != null && (
            <Receipt
              origin="browser · decoded and re-encoded to 16-bit PCM WAV"
              detail={`${formatSeconds(duration)} captured`}
            />
          )}
        </CardBody>
      </Card>

      {result === null ? (
        <Card className="min-w-0">
          <CardBody>
            <SceneState name="empty" size="md">
              <p className="text-sm text-muted-foreground">
                Record a clip or upload a file to transcribe it.
              </p>
            </SceneState>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── What the call actually measured ────────────────────────────── */}
          <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {/* `duration_seconds` is nullable — a container the server could not
                measure has no duration, and `0:00` would assert an empty clip. */}
            {result.duration_seconds == null ? (
              <Absence
                figure="Clip duration"
                why="The server could not read a duration from this container."
                needed="a decodable PCM WAV, which the browser produces when it can decode the recording"
              />
            ) : (
              <StatCard
                label="Clip duration"
                value={formatSeconds(result.duration_seconds)}
                icon={Clock}
                tone="neutral"
              />
            )}
            <StatCard
              label="Audio seconds billed"
              value={`${SECONDS.format(result.audio_seconds_billed)}s`}
              icon={ReceiptIcon}
              tone="ml"
              source="audio_seconds_billed · the unit this deployment bills"
            />
            <StatCard
              label="Segments"
              value={COUNT.format(result.segments.length)}
              icon={Layers}
              tone="graph"
              source={`${COUNT.format(result.chunk_count)} transcription request${result.chunk_count === 1 ? '' : 's'}`}
            />
            <StatCard
              label="Transcription cost"
              value={USD.format(result.cost_usd)}
              icon={Coins}
              tone="neutral"
              source={`aegis.voice · ${result.model || 'model not reported'}`}
            />
          </div>

          {/* ── The one within-clip series ─────────────────────────────────── */}
          <Card className="min-w-0">
            <CardHeader
              as="h3"
              eyebrow="segments[].start → end"
              title="Where the speech is"
              actions={
                result.chunk_count > 1 ? (
                  <Badge tone="neutral" className="font-mono">
                    {COUNT.format(result.chunk_count)} chunks
                  </Badge>
                ) : null
              }
            />
            <CardBody className="min-w-0 space-y-4 pt-4">
              {timeline.segments.length === 0 || timeline.span <= 0 ? (
                <Absence
                  figure="Segment timeline"
                  why="No segment carries both a start and an end, so there is nothing to place on a clock."
                  needed="a provider that returns per-segment timings"
                />
              ) : (
                <SegmentTimeline
                  segments={timeline.segments}
                  span={timeline.span}
                  chunkCount={result.chunk_count}
                />
              )}
              <Receipt
                origin="segments[].start / end · gateway transcription"
                detail={
                  timeline.untimed > 0
                    ? `${COUNT.format(timeline.untimed)} untimed segment${timeline.untimed === 1 ? '' : 's'} not placed`
                    : undefined
                }
              />
            </CardBody>
          </Card>

          <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
            <Card className="min-w-0">
              <CardHeader as="h3" eyebrow="evidence · never input" title="Transcript" />
              <CardBody className="min-w-0 pt-4">
                <TranscriptPanel result={result} />
              </CardBody>
            </Card>

            <div className="min-w-0 space-y-6">
              <Card className="min-w-0">
                <CardBody className="min-w-0">
                  <RailVerdict result={result} />
                </CardBody>
              </Card>

              <Card className="min-w-0">
                <CardHeader
                  as="h3"
                  eyebrow="agent_input · not transcript"
                  title="Send to the agent"
                  actions={
                    <Button
                      onClick={sendToAgent}
                      disabled={result.agent_input == null || agentRun?.status === 'running'}
                    >
                      <SendHorizontal className="size-4" aria-hidden />
                      {agentRun?.status === 'running' ? 'Running…' : 'Run'}
                    </Button>
                  }
                />
                <CardBody className="min-w-0 space-y-3 pt-4">
                  {agentRun === null ? (
                    result.agent_input == null ? (
                      /* "Data held behind a lock" — the rails refused this text,
                         and the words below are what actually says so. */
                      <SceneState name="sealed" size="sm">
                        <p className="text-sm text-muted-foreground">
                          Blocked by the rails — there is nothing to send.
                        </p>
                      </SceneState>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        Runs the full agent pipeline on the rails-cleared text.
                      </p>
                    )
                  ) : (
                    <div className="min-w-0 space-y-2" aria-live="polite">
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
                      <p className="rounded-lg border border-border bg-surface-2/30 px-3.5 py-3 text-sm leading-relaxed break-words text-foreground">
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

          {/* ── The same segments, as the list the picture is drawn from ───── */}
          <DataPanel
            as="h3"
            eyebrow="segments[]"
            title="Segments"
            maxHeight={420}
            actions={
              <Badge tone={result.has_confidence ? 'ml' : 'neutral'} className="font-mono">
                {COUNT.format(result.segments.length)}
              </Badge>
            }
            footer={
              /* The fleet's Whisper returns id/start/end/text and nothing else.
                 A "Confidence" column of dashes reads as a broken column; the
                 absence is stated once, in the slot the column would occupy. */
              result.has_confidence ? null : (
                <Absence
                  className="w-full"
                  figure="Per-segment confidence"
                  why="This Whisper deployment reports none."
                  needed="a provider that returns a per-segment probability"
                />
              )
            }
          >
            {result.segments.length === 0 ? (
              <SceneState name="empty" size="sm">
                <p className="text-sm text-muted-foreground">
                  The transcription returned no segments.
                </p>
              </SceneState>
            ) : (
              <Table className="min-w-[520px]">
                <THead>
                  <TH>time</TH>
                  {result.chunk_count > 1 ? <TH>chunk</TH> : null}
                  <TH>segment</TH>
                  {result.has_confidence ? <TH className="text-right">confidence</TH> : null}
                </THead>
                <TBody>
                  {result.segments.map((seg) => (
                    <TR key={seg.index} className="align-top">
                      <TD>
                        <Figure className="whitespace-nowrap text-muted-foreground">
                          {formatSeconds(seg.start)}–{formatSeconds(seg.end)}
                        </Figure>
                      </TD>
                      {result.chunk_count > 1 ? (
                        <TD>
                          <Figure className="text-muted-foreground">
                            #{COUNT.format(seg.chunk)}
                          </Figure>
                        </TD>
                      ) : null}
                      <TD className="text-foreground">
                        <span className="break-words">{seg.text}</span>
                      </TD>
                      {result.has_confidence ? (
                        <TD className="text-right">
                          {seg.confidence == null ? (
                            <span className="text-xs text-muted-foreground">not reported</span>
                          ) : (
                            <Figure className="text-blue-800">
                              {PERCENT.format(seg.confidence)}
                            </Figure>
                          )}
                        </TD>
                      ) : null}
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </DataPanel>
        </>
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
