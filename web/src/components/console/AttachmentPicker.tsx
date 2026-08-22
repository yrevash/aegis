'use client'

import { ImageUp, Loader2, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { ImageDropzone, type PickedImage } from '@/components/vision/ImageDropzone'
import { uploadAttachment } from '@/lib/api/console'
import { cn } from '@/lib/utils'

import { ComposerMenu } from './ComposerMenu'
import { attachmentVerdict, toTurnAttachment, type TurnAttachment } from './composerAttachment'

/**
 * Attach an image to the next question.
 *
 * Entirely reuse: the picker is `ImageDropzone` from the vision screen, decoding the
 * file locally and reporting its natural size, and the screening is `POST /attachments`
 * — the same ordered rails the vision page runs, in the same order. Nothing new is
 * built here and nothing is stored: the attachment lives for one run.
 *
 * The screen runs **before** the question is sent, not with it. That ordering is the
 * point: if the injection screen refuses the image, the person finds out while they can
 * still do something about it, and the refused description never reaches the model.
 */
export function AttachmentPicker({
  token,
  question,
  attachment,
  onAttach,
  onClear,
  disabled,
}: {
  token: string | null
  /** What is in the box right now — asked of the vision model as the image's question. */
  question: string
  attachment: TurnAttachment | null
  onAttach: (attachment: TurnAttachment) => void
  onClear: () => void
  disabled: boolean
}): ReactElement {
  const [screening, setScreening] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const screen = (image: PickedImage): void => {
    setError(null)
    setScreening(true)
    void uploadAttachment(token, {
      image_base64: image.base64,
      mime_type: image.mime,
      question: question.trim() === '' ? undefined : question.trim(),
      filename: image.name,
    })
      .then((response) => onAttach(toTurnAttachment(response, image.dataUrl)))
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : 'The image could not be screened.'),
      )
      .finally(() => setScreening(false))
  }

  const verdict = attachment === null ? null : attachmentVerdict(attachment)
  const value = screening
    ? 'Screening…'
    : verdict === null
      ? 'None'
      : verdict.blocked
        ? 'Refused'
        : (attachment?.filename ?? 'Attached')

  return (
    <ComposerMenu
      label="Image"
      value={value}
      disabled={disabled}
      icon={<ImageUp aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />}
    >
      {attachment === null ? (
        <>
          <p className="eyebrow">Attach an image</p>
          <p className="mt-1 mb-3 text-[0.74rem] leading-snug text-muted-foreground">
            It is screened before it is sent: payload hygiene, the injection screen, image
            PII, then the vision model. Nothing is stored — the attachment lives for one
            run.
          </p>
          {screening ? (
            <p className="flex items-center gap-2 rounded-lg border border-dashed border-border bg-surface-2/40 px-6 py-12 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
              Screening the image…
            </p>
          ) : (
            <ImageDropzone onPick={screen} onError={setError} disabled={disabled} />
          )}
          {error !== null && (
            <p className="mt-2 rounded-lg border border-block/50 bg-block/10 px-2.5 py-2 text-[0.76rem] text-block-ink">
              {error}
            </p>
          )}
        </>
      ) : (
        <>
          <div className="flex items-start gap-3">
            {/* eslint-disable-next-line @next/next/no-img-element -- a local data: URL, never a remote asset */}
            <img
              src={attachment.previewUrl}
              alt={attachment.filename ?? 'The attached image'}
              className="size-16 shrink-0 rounded-lg border border-border object-cover"
            />
            <div className="min-w-0 flex-1">
              <p className="truncate text-[0.8rem] font-medium text-foreground">
                {attachment.filename ?? 'Attached image'}
              </p>
              <p className="font-mono text-[0.7rem] text-muted-foreground">
                {attachment.mimeType ?? 'type not sniffed'}
              </p>
            </div>
            <button
              type="button"
              onClick={onClear}
              className="rounded-md p-1 text-muted-foreground outline-none transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X aria-hidden className="size-4" />
              <span className="sr-only">Remove the image</span>
            </button>
          </div>

          {verdict !== null && (
            <p
              className={cn(
                'mt-3 flex items-start gap-1.5 rounded-lg px-2.5 py-2 text-[0.76rem] leading-snug',
                verdict.blocked
                  ? 'border border-block/50 bg-block/10 text-block-ink'
                  : 'border border-border bg-surface-2/50 text-muted-foreground',
              )}
            >
              {verdict.blocked ? (
                <ShieldAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              ) : (
                <ShieldCheck aria-hidden className="mt-0.5 size-3.5 shrink-0" />
              )}
              <span>
                <span className="font-medium">{verdict.label}.</span>{' '}
                {/* The reason first, the coverage after: WHY it refused is what the person
                    can act on; WHICH controls ran is the evidence behind it. */}
                {verdict.reason !== '' ? `${verdict.reason} ` : ''}
                {verdict.detail}
              </span>
            </p>
          )}

          <p className="mt-2 text-[0.74rem] leading-snug text-muted-foreground">
            {verdict?.blocked === true
              ? 'The description is not carried into the run. Read the refusal above before you retry: an image the screen flagged will be refused again, while a screen that could not run may pass on a second attempt.'
              : 'The screened description goes with your next question, labelled as evidence.'}
          </p>

          <p className="mt-2 max-h-28 overflow-y-auto rounded-lg border border-border bg-surface-2/40 px-2.5 py-2 text-[0.74rem] leading-snug whitespace-pre-wrap text-muted-foreground">
            {/* A blocked attachment has no description by design — the model never ran —
                so the box carries the rail's sentence instead of the empty-state line,
                which is what made a refusal read as "nothing happened". */}
            {attachment.summary.trim() !== ''
              ? attachment.summary
              : attachment.blockedReason.trim() !== ''
                ? attachment.blockedReason
                : 'No description was produced.'}
          </p>
        </>
      )}
    </ComposerMenu>
  )
}
