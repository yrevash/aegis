'use client'

import { ImageUp } from 'lucide-react'
import { useCallback, useRef, useState, type DragEvent, type ReactElement } from 'react'

import { cn } from '@/lib/utils'

/** An image the operator picked, decoded far enough to send and to draw over. */
export interface PickedImage {
  /** Original filename, carried to the audit log. */
  name: string
  /** The browser's declared MIME type. The backend verifies it against magic bytes. */
  mime: string
  /** Bytes, base64-encoded (no `data:` prefix). */
  base64: string
  /** A `data:` URL for the preview. */
  dataUrl: string
  /** Natural pixel size — the coordinate space detected PII regions are reported in. */
  width: number
  height: number
  bytes: number
}

/** Decode a picked file into everything the request and the overlay need. */
async function readImage(file: File): Promise<PickedImage> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('Could not read that file.'))
    reader.readAsDataURL(file)
  })
  const size = await new Promise<{ width: number; height: number }>((resolve, reject) => {
    const probe = new Image()
    probe.onload = () => resolve({ width: probe.naturalWidth, height: probe.naturalHeight })
    probe.onerror = () => reject(new Error('That file is not an image the browser can decode.'))
    probe.src = dataUrl
  })
  return {
    name: file.name,
    mime: file.type || 'application/octet-stream',
    base64: dataUrl.split(',')[1] ?? '',
    dataUrl,
    width: size.width,
    height: size.height,
    bytes: file.size,
  }
}

/**
 * Drag-and-drop / click-to-browse image picker.
 *
 * It decodes the file locally and reports the natural pixel size alongside the
 * bytes, because that is the coordinate space the backend reports detected-PII
 * regions in — without it the overlay would be guesswork.
 *
 * It deliberately does **no** validation beyond "the browser could decode it".
 * Size caps, MIME truth and the decompression-bomb guard are server-side
 * controls; re-implementing a weaker copy here would teach an operator that the
 * client is the gate, and the client is never the gate.
 */
export function ImageDropzone({
  onPick,
  onError,
  disabled = false,
}: {
  onPick: (image: PickedImage) => void
  onError: (message: string) => void
  disabled?: boolean
}): ReactElement {
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const take = useCallback(
    (file: File | undefined | null) => {
      if (!file) return
      readImage(file)
        .then(onPick)
        .catch((err: Error) => onError(err.message))
    },
    [onPick, onError],
  )

  function onDrop(event: DragEvent<HTMLDivElement>): void {
    event.preventDefault()
    setOver(false)
    if (disabled) return
    take(event.dataTransfer.files?.[0])
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed px-6 py-12 text-center transition-colors',
        over ? 'border-agent bg-agent/10' : 'border-border bg-surface-2/40',
        disabled && 'opacity-60',
      )}
    >
      <ImageUp className="size-7 text-muted-foreground" />
      <div>
        <p className="text-sm font-medium text-foreground">Drop an image here</p>
        <p className="mt-1 text-[0.72rem] text-muted-foreground">PNG, JPEG, WebP or GIF</p>
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        className="rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium text-foreground shadow-card transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
      >
        Browse files
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          take(e.target.files?.[0])
          e.target.value = ''
        }}
      />
    </div>
  )
}
