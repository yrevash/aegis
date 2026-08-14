# Voice — the theory

Digital audio, the WAV container, the transformer that reads a spectrogram, the
arithmetic of the silence splitter, and how ASR quality is actually measured.

---

## 1. Digital audio, precisely

Three numbers define an uncompressed audio stream, and every one of them shows up in
the code:

**Sample rate** — measurements per second. Telephony is 8 kHz, speech ASR is usually
16 kHz, CD audio is 44.1 kHz. The **Nyquist–Shannon** theorem sets the ceiling: you can
faithfully represent frequencies up to half the sample rate. At 16 kHz you capture up to
8 kHz, which covers essentially all speech intelligibility (the phone network's 8 kHz /
4 kHz ceiling is why "s" and "f" are hard to tell apart on a call).

**Sample width** — bytes per sample. 16-bit signed (`-32768…32767`) is the norm. 8-bit
is **unsigned** with a midpoint of 128, which is a real trap: treat 8-bit samples as
signed and every value is positive and large, so every window looks equally loud and no
pause is ever found. You must subtract the midpoint first.

**Channels** — mono, stereo, more. Samples are **interleaved**: `L R L R L R`. Which
matters for indexing: a "frame" is one sample per channel, so the byte offset of frame
*n* is `n × channels × sample_width`.

Multiply the three and you get the data rate. 16 kHz × 2 bytes × 1 channel = 32 KB per
second, about 1.9 MB per minute of uncompressed mono speech.

---

## 2. The WAV container

**RIFF/WAVE** is one of the few audio containers simple enough to parse in a hundred
lines. Structure:

```
"RIFF"  <4-byte size>  "WAVE"
  "fmt "  <size>  format code, channels, sample rate, byte rate, block align, bits
  "data"  <size>  ...raw interleaved PCM samples...
```

Format code `1` means uncompressed PCM. Everything is little-endian.

Python's stdlib `wave` module reads exactly this: `getparams()` returns channels,
sample width, frame rate and frame count; `readframes(n)` gives you the raw bytes; and
`wave.open(..., "wb")` writes a new one. Combined with `array`, which reinterprets a
byte buffer as machine integers, you can decode, analyse and re-encode PCM WAV with
**no third-party dependency at all**.

The limits are honest ones and must be reported rather than hidden:

- **MP3, OGG, FLAC, M4A are compressed.** The stdlib cannot decode them. Decoding needs
  a codec — in practice `ffmpeg`.
- **24-bit WAV exists** and `array` has no type code for three-byte integers. So 1, 2
  and 4 bytes per sample are readable and 3 is not.
- WAV files can carry other chunk types and non-PCM format codes; `wave` raises on
  those.

A splitter built on the stdlib therefore covers *PCM WAV only*, and everything else must
be transcribed whole — **and say so**. A plan that returns one chunk because it could
not parse the container must not look like a plan that returned one chunk because the
recording was short.

---

## 3. Whisper, architecturally

**Input.** The waveform is resampled to 16 kHz and converted to a **log-Mel
spectrogram**: 80 frequency bands, one frame every 10 ms. A spectrogram is a
time-frequency image — a short-time Fourier transform windowed across the signal, with
the frequency axis warped onto the **Mel scale**, which spaces bands the way human
pitch perception does (roughly linear below 1 kHz, logarithmic above).

So the model's input is, quite literally, a picture of the sound.

**Encoder.** Two 1-D convolutions, then sinusoidal position embeddings, then a stack of
standard transformer blocks with self-attention. Output: a sequence of audio
representations.

**Decoder.** An autoregressive transformer that generates text tokens, with
cross-attention onto the encoder output. This is where the language modelling happens —
it is what resolves "their/there/they're" from context, and it is why Whisper needs no
separate language model.

**Task tokens.** The decoder is prompted with special tokens that select the behaviour:
`<|transcribe|>` vs `<|translate|>`, a language tag, `<|notimestamps|>` or not. One set
of weights, several jobs.

**The 30-second window.** The encoder takes a fixed 3000-frame input — exactly 30
seconds. Shorter audio is zero-padded. Longer audio is processed in successive windows
with the previous transcript fed back as context. Everything about long-form
transcription — chunk boundaries, timestamp drift, repetition loops — traces back to
this constant.

**Training.** 680,000 hours of `(audio, transcript)` pairs scraped from the web, with
automated filtering rather than human curation. That is *weak supervision*: the labels
are imperfect, but there are two orders of magnitude more of them than any curated
corpus. The Whisper paper's central result is that this trades benchmark-topping
accuracy for **robustness** — competitive zero-shot performance across accents,
domains and noise conditions, where models tuned on LibriSpeech collapse off-domain.

**Known failure modes**, all consequences of the above:

- **Silence hallucination.** Web audio often carries subtitles over quiet passages, so
  the model learned that silence can have text. Given actual silence, it invents.
- **Repetition loops.** Autoregressive decoding can enter a cycle, emitting the same
  phrase indefinitely.
- **Timestamp drift** on long audio, from window stitching.
- **Uneven language quality**, tracking the training distribution.

---

## 4. `verbose_json`, and the confidence that is not there

The OpenAI-compatible transcription API takes a `response_format`. `text` returns a bare
string. **`verbose_json`** returns a structured object:

```jsonc
{
  "text": "...",
  "language": "en",
  "duration": 87.3,
  "segments": [
    { "id": 0, "start": 0.0, "end": 4.2, "text": "...",
      "avg_logprob": -0.31, "no_speech_prob": 0.02,
      "compression_ratio": 1.4, "temperature": 0.0, "seek": 0, "tokens": [...] }
  ]
}
```

Two fields matter beyond the obvious.

**`duration`** is the billing unit. Without `verbose_json` you do not get it, and
without it you cannot price a per-minute call from the response alone.

**`avg_logprob` and `no_speech_prob`** are the closest thing to a confidence signal —
and neither is a calibrated probability that the transcription is *correct*.
`avg_logprob` is the mean log-probability of the emitted tokens under the model's own
distribution; a fluent hallucination scores well on it. `no_speech_prob` is the model's
estimate that the segment contains no speech at all, which is useful for filtering, not
for grading.

Common practice is `exp(avg_logprob)` presented as a percentage. That number is a
**model-internal likelihood, not an accuracy estimate**, and displaying it as
"confidence: 73%" is an overclaim of exactly the kind this platform bans. The honest
options are: carry the raw field and label it accurately, or report *no confidence* and
say so. Deriving a plausible-looking percentage is the one thing you must not do.

---

## 5. The silence splitter, mathematically

### The envelope

Partition the samples into windows of `W` samples and compute the mean square of each:

```
E[k] = (1/W) * Σ  x[i]²      for i in window k
```

Root-mean-square is the conventional loudness measure, but **the square root is
monotonic** — it changes no comparison a splitter makes, so skipping it costs nothing
and saves a call per window.

Window length: 20 ms is the standard speech analysis frame. Long enough that a single
glottal pulse does not dominate; short enough to locate a pause to within a syllable.
At 16 kHz that is 320 samples per channel.

### The threshold

Absolute thresholds fail because recording level varies enormously. So the threshold is
**relative to the clip's own mean**:

```
threshold = mean(E) * ratio²
```

The square is there because `E` is a mean square, not an RMS — a ratio expressed in
amplitude terms (say 0.18, i.e. 18% of average amplitude) must be squared to compare
against energy. Getting this wrong by omitting the square gives you a threshold ~5×
too high, and everything looks like silence.

### The minimum run

A window below threshold is not a pause. A *run* of them is. Requiring
`min_silence_seconds` (a quarter of a second is a reasonable floor) distinguishes a
sentence boundary from the stop closure inside "butter".

### Boundary selection

Walk the recording once with a cursor:

```
while remaining > max_chunk:
    target = cursor + max_chunk
    floor  = max(cursor + min_chunk, target - search_window)
    mid    = longest sub-threshold run in [floor, target), take its centre
    if mid exists and mid > cursor:  cut at mid, silence=True
    else:                            cut at target, silence=False
    cursor = cut
```

Four parameters and each earns its place:

- **`max_chunk_seconds`** — the ceiling. Keeps a request short enough that a stall is
  cheap to retry and comfortably inside any hosted duration limit.
- **`min_chunk_seconds`** — a floor, so a long silence cannot produce a stream of
  near-empty requests (each of which still costs a round trip).
- **`search_seconds`** — how far back to hunt. Unbounded search would let a chunk shrink
  arbitrarily; bounded search means you sometimes cut on time, which is fine as long as
  you *report* it.
- **`silence_ratio`, `min_silence_seconds`** — what counts as a pause.

Searching **backwards** from the target rather than forwards is deliberate: it keeps
every chunk at or under the ceiling. Searching forward could exceed it.

### Stitching

Chunk *k* starts at offset `t_k`. Every segment from it must be shifted:

```
segment.start += t_k
segment.end   += t_k
```

and segments renumbered across the whole transcript. Without the shift, all timestamps
after chunk 0 are wrong and a click-to-seek UI lands in the wrong place.

---

## 6. Measuring ASR quality

**Word Error Rate** is the standard, computed from the Levenshtein alignment of
hypothesis to reference:

```
WER = (Substitutions + Insertions + Deletions) / N_reference_words
```

Notes that matter in practice:

- WER can exceed 100% — insertions are unbounded. A repetition loop produces a
  spectacular WER.
- It is **unnormalised for meaning**. "$100" vs "one hundred dollars" is three errors
  and zero misunderstanding. Serious evaluation normalises text first (Whisper ships
  such a normaliser), and the normaliser choice materially moves the number.
- All errors weigh the same. Getting the customer's account number wrong and dropping
  an "um" both count as one.

**CER** (character error rate) is used for languages without clear word boundaries.

**Diarisation** — *who* spoke, not what — is a separate problem with its own metric
(Diarisation Error Rate). It needs either a provider that reports speaker labels or a
separate embedding-and-clustering model. If you have neither, you have no speaker
attribution, and the honest move is to **say so** rather than let a UI imply it.

---

## 7. Cost, latency, and the shape of the request

**Cost.** Per audio-minute. Round in the direction the provider does; if the provider
does not report a duration and the container did not either, the call is *unpriced* —
a distinct state from free, and one that must be visible.

**Latency.** Roughly proportional to audio duration, with a fixed overhead per request.
Which means chunking has a real cost: N chunks means N round trips. Chunks that are too
small pay overhead repeatedly; too large and a single stall hurts.

**Sequential or parallel?** Parallel chunk transcription is faster and gives up two
things: strict cost ordering under a budget cap (concurrent calls can race past a
limit), and the ability to stream partial results in order. Sequential is slower, and
lets each chunk be checked against the budget before it is spent and streamed to the UI
as it arrives.

**The upload shape.** OpenAI-compatible transcription is a **multipart file upload**,
not a JSON `messages` array — a genuinely different API shape from chat completion.
Client libraries derive the filename (and therefore the format hint) from the file
handle's `name` attribute, which is why an in-memory buffer needs a name attached or the
part is uploaded with no extension and the provider has to guess at the container.

Multipart also beats base64-in-JSON for a real reason: base64 inflates by ~33%, and it
forces the entire recording to be materialised as one string on both sides before
anything can inspect it — which defeats a streaming size check.

---

## 8. Why transcribe-then-guard is the right architecture

Two options for guarding speech:

**A parallel audio policy.** Audio-specific detectors, audio-specific classifiers. You
would be re-implementing signature matching, injection detection, PII detection, content
safety and topical screening against a *weaker signal* than text — and then maintaining
two policy stacks that drift the moment someone updates one.

**Transcribe first, then reuse the text stack.** Every attack that works typed works
spoken. The text rails are the mature ones. The transcript is text.

The second wins on every axis, and it has a property the first cannot match: **every
custom rail an operator has already written applies to speech automatically**, with no
porting. On the day voice is switched on, their domain policy covers it.

The trade is honest and worth stating: you are guarding the *transcription*, not the
audio. Anything ASR drops — tone, a whispered aside the model missed, a speaker the
model did not separate — is invisible to the rails. That is the cost of the reuse, and
it is much smaller than the cost of a second, weaker policy stack.

---

## What you should now be able to explain

- Sample rate, width, channels — and why 8-bit unsigned defeats a naive splitter
- Nyquist, and why 16 kHz is the ASR standard
- The WAV/RIFF layout, and exactly what the stdlib can and cannot decode
- Whisper's encoder-decoder architecture and the log-Mel spectrogram input
- The 30-second window, and every long-form failure that traces back to it
- Why weak supervision at scale bought robustness rather than benchmark wins
- What `verbose_json` carries, and why `avg_logprob` is not a confidence score
- The envelope, the squared relative threshold, and the four splitter parameters
- Why you search backwards from the target boundary
- WER, its blind spots, and why diarisation is a separate problem
- Why transcription is multipart rather than JSON, and why the handle needs a name
- The architectural case for transcribe-then-guard, and the cost it accepts

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
