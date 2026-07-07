import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Coerce a thrown value (`unknown` in a catch clause) to a display string.
 * Centralizes the `e instanceof Error ? e.message : String(e)` idiom that was
 * copy-pasted across ~18 catch handlers (pages, forms, hooks). One home means a
 * future change to error presentation (e.g. special-casing a typed API error)
 * happens in a single place.
 */
export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
