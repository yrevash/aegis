import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge conditional class names and de-duplicate conflicting Tailwind utilities.
 *
 * @param inputs - Class values (strings, arrays, or conditionals).
 * @returns A single merged, conflict-resolved class string.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
