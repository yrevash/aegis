import { useEffect, useRef, useState, type RefObject } from 'react'

/** A width/height pair in CSS pixels. */
export interface Size {
  width: number
  height: number
}

/**
 * Observe an element's size. Returns a ref to attach and the live dimensions —
 * used to size the canvas graph to its container (react-force-graph needs
 * explicit pixel width/height).
 */
export function useElementSize<T extends HTMLElement>(): [RefObject<T | null>, Size] {
  const ref = useRef<T>(null)
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })

  useEffect(() => {
    const el = ref.current
    if (el === null) return
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setSize({
          width: Math.floor(entry.contentRect.width),
          height: Math.floor(entry.contentRect.height),
        })
      }
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return [ref, size]
}
