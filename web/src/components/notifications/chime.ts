/**
 * The optional sound. Off by default, and the smallest thing that could work.
 *
 * It was asked for as "if we can, a simple one; if not, no issues", which is the whole
 * specification and also the reason this file is forty lines and imports nothing. Two
 * oscillator notes through the Web Audio API — no asset to fetch, no library, no
 * decoding, nothing to go wrong on a machine with no speaker.
 *
 * Three refusals, all of them non-negotiable:
 *
 * - **Off unless the person turned it on.** A console that beeps at a jury nobody
 *   warned is worse than silence.
 * - **Never while the tab is hidden.** A sound from a tab you cannot see has no
 *   referent; it is just noise from a browser.
 * - **Never under `prefers-reduced-motion`.** The setting is the closest signal a
 *   browser gives for "do not surprise me", and vestibular and attention conditions
 *   are why people set it.
 */

/** Where the choice is remembered. Per browser, like every other viewer preference. */
export const CHIME_KEY = 'aegis.alertSound'

/** Whether the sound is on. Absent, unreadable storage and junk all mean off. */
export function chimeEnabled(storage: Pick<Storage, 'getItem'> | null): boolean {
  try {
    return storage?.getItem(CHIME_KEY) === 'on'
  } catch {
    return false
  }
}

/** Remember the choice. A storage that refuses to write is not an error worth raising. */
export function persistChime(storage: Pick<Storage, 'setItem'> | null, on: boolean): void {
  try {
    storage?.setItem(CHIME_KEY, on ? 'on' : 'off')
  } catch {
    /* private mode, or storage disabled — the toggle still holds for this tab */
  }
}

/**
 * Play it, if every condition holds. Silent — never throwing — when any does not.
 *
 * @param enabled - The person's own setting.
 */
export function playChime(enabled: boolean): void {
  if (!enabled) return
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  if (document.visibilityState === 'hidden') return
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true) return
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (Ctor === undefined) return
  try {
    const audio = new Ctor()
    const gain = audio.createGain()
    gain.connect(audio.destination)
    gain.gain.setValueAtTime(0.0001, audio.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.05, audio.currentTime + 0.01)
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.34)
    const tone = audio.createOscillator()
    tone.type = 'sine'
    tone.frequency.setValueAtTime(880, audio.currentTime)
    tone.frequency.setValueAtTime(1174.7, audio.currentTime + 0.09)
    tone.connect(gain)
    tone.start()
    tone.stop(audio.currentTime + 0.35)
    tone.onended = () => void audio.close().catch(() => undefined)
  } catch {
    /* an autoplay policy or a machine with no output device: silence is the fallback */
  }
}
