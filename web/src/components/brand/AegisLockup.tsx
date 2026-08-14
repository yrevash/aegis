import { AegisMark } from '@/components/brand/AegisMark'
import { cn } from '@/lib/utils'

/**
 * The Aegis lockup: the falcon in ink beside the wordmark.
 *
 * The mark is deliberately **not** set in a dark rounded tile. Boxing it shrank
 * the wingspan to fit the square and buried the silhouette; standing free in
 * `currentColor` it reads as the bird it is, and the lockup carries more
 * presence at the same footprint.
 *
 * One component so the five places that show the brand — sidebar, both login
 * headers, the landing header and the footer — cannot drift apart.
 */

const SIZES = {
  sm: { mark: 26, word: 'text-[1.02rem]' },
  md: { mark: 32, word: 'text-[1.18rem]' },
  lg: { mark: 40, word: 'text-[1.4rem]' },
} as const

export interface AegisLockupProps {
  size?: keyof typeof SIZES
  className?: string
}

export function AegisLockup({ size = 'md', className }: AegisLockupProps) {
  const s = SIZES[size]
  return (
    <span className={cn('flex items-center gap-2.5 text-foreground', className)}>
      <AegisMark width={s.mark} />
      <span className={cn('font-semibold tracking-tight', s.word)}>Aegis</span>
    </span>
  )
}
