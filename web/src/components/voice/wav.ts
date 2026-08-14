/**
 * Browser-side audio helpers — re-encode a recording as PCM WAV and read its peaks.
 *
 * Why this exists rather than uploading the `MediaRecorder` blob directly:
 *
 * 1. **The accepted container list.** `aegis.media` sniffs magic bytes and accepts
 *    wav / mp3 / ogg / flac / m4a. Chrome's `MediaRecorder` produces WebM/Opus,
 *    which is not on that list and would be refused by payload hygiene — correctly,
 *    since nothing server-side can parse it.
 * 2. **Chunking.** The server splits long recordings on silence using the stdlib
 *    WAV parser (policy is fleet models only — no ffmpeg, no local codec), so a
 *    long recording only chunks if it arrives as PCM WAV.
 *
 * The conversion runs on the platform's own decoder (`AudioContext.decodeAudioData`),
 * so it adds no dependency. When the browser cannot decode its own recording,
 * {@link toWav} returns `null` and the caller says so — it never silently uploads a
 * container the server will reject and calls that a transcription failure.
 */

/** A recording converted for upload, plus what was measured on the way. */
export interface PreparedAudio {
  /** The recording as 16-bit PCM WAV. */
  wav: Blob
  /** Duration in seconds, measured from the decoded buffer. */
  duration: number
  /** Normalised 0..1 peak per display bucket, for the static waveform. */
  peaks: number[]
  /** Sample rate of the decoded buffer. */
  sampleRate: number
}

/** Downmix every channel of `buffer` to one mono Float32Array. */
function downmix(buffer: AudioBuffer): Float32Array {
  const { numberOfChannels, length } = buffer
  if (numberOfChannels === 1) return buffer.getChannelData(0)
  const out = new Float32Array(length)
  for (let c = 0; c < numberOfChannels; c += 1) {
    const data = buffer.getChannelData(c)
    for (let i = 0; i < length; i += 1) out[i] += data[i] / numberOfChannels
  }
  return out
}

/** Encode mono float samples as a 16-bit PCM RIFF/WAVE file. */
function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const bytes = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(bytes)
  const ascii = (offset: number, text: string): void => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true) // PCM header size
  view.setUint16(20, 1, true) // format: PCM
  view.setUint16(22, 1, true) // channels: mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true) // byte rate
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  ascii(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true)
  }
  return new Blob([bytes], { type: 'audio/wav' })
}

/** Reduce samples to `count` normalised peak buckets for the static waveform. */
function peaksOf(samples: Float32Array, count: number): number[] {
  const per = Math.max(1, Math.floor(samples.length / count))
  const out: number[] = []
  let max = 0
  for (let b = 0; b < count; b += 1) {
    let peak = 0
    const start = b * per
    for (let i = start; i < Math.min(start + per, samples.length); i += 1) {
      const v = Math.abs(samples[i])
      if (v > peak) peak = v
    }
    out.push(peak)
    if (peak > max) max = peak
  }
  // Normalised against the clip's own loudest bucket: a quiet laptop microphone
  // should still draw a readable waveform rather than a flat line.
  return max > 0 ? out.map((p) => p / max) : out
}

/**
 * Decode `blob` with the browser's own decoder and re-encode it as PCM WAV.
 *
 * @param blob - The recording (any container the browser can decode).
 * @param buckets - How many peak buckets to produce for the waveform.
 * @returns The prepared audio, or `null` when the browser could not decode it.
 */
export async function toWav(blob: Blob, buckets = 160): Promise<PreparedAudio | null> {
  const Ctor =
    typeof window === 'undefined'
      ? undefined
      : window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  const ctx = new Ctor()
  try {
    const buffer = await ctx.decodeAudioData(await blob.arrayBuffer())
    const mono = downmix(buffer)
    return {
      wav: encodeWav(mono, buffer.sampleRate),
      duration: buffer.duration,
      peaks: peaksOf(mono, buckets),
      sampleRate: buffer.sampleRate,
    }
  } catch {
    return null
  } finally {
    void ctx.close()
  }
}

/** Format seconds as `m:ss`, or an em dash when the value is unknown. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const whole = Math.floor(seconds)
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}
