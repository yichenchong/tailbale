import { useEffect, useState } from "react"

/** @internal exported for test cleanup */
export let cachedTimezone: string | null = null
export function _resetTimezoneCache() { cachedTimezone = null }
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

/**
 * Returns the configured timezone from settings (e.g. "America/New_York").
 * Falls back to the browser's local timezone until settings are loaded.
 */
export function useTimezone(): string {
  const [tz, setTz] = useState(cachedTimezone ?? BROWSER_TZ)

  useEffect(() => {
    if (cachedTimezone) return
    // Use raw fetch to avoid api.get throwing on non-200 responses
    // (which would break tests that mock fetch globally).
    fetch("/api/settings", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.general?.timezone) {
          cachedTimezone = data.general.timezone
          setTz(data.general.timezone)
        }
      })
      .catch(() => {})
  }, [])

  return tz
}

/** Format a date string or Date using the configured timezone. */
export function formatDateTime(
  date: string | Date | null | undefined,
  timezone: string,
  options?: Intl.DateTimeFormatOptions,
): string {
  if (!date) return ""
  const d = typeof date === "string" ? new Date(date) : date
  if (isNaN(d.getTime())) return ""
  return d.toLocaleString(undefined, { timeZone: timezone, ...options })
}

/** Format a date (no time) using the configured timezone. */
export function formatDate(
  date: string | Date | null | undefined,
  timezone: string,
): string {
  return formatDateTime(date, timezone, { dateStyle: "medium" })
}

/** Format a time (no date) using the configured timezone. */
export function formatTime(
  date: string | Date | null | undefined,
  timezone: string,
): string {
  return formatDateTime(date, timezone, { timeStyle: "medium" })
}
