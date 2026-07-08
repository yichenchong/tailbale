import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Coerce a thrown value (`unknown` in a catch clause) to a display string.
 * With a fallback, non-Error values use the fallback; without one, the legacy
 * single-argument behavior (`Error.message` else `String(e)`) is preserved.
 */
export function errorMessage(e: unknown, fallback = ""): string {
  if (e instanceof Error) return e.message
  return fallback || String(e)
}
