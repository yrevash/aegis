/**
 * Minimal className joiner — no clsx/tailwind-merge dependency in the scaffold.
 * Filters falsy values and joins with a space.
 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
