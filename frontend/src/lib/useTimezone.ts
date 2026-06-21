import { useEffect, useState } from "react"

/** @internal exported for test cleanup */
export let cachedTimezone: string | null = null
const subscribers = new Set<(tz: string) => void>()
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

export function _resetTimezoneCache() {
  cachedTimezone = null
}

/**
 * Update the configured timezone and notify every mounted useTimezone() hook
 * so timestamps re-render immediately (e.g. after saving the General settings)
 * instead of staying stale until a full page reload.
 */
export function setConfiguredTimezone(tz: string): void {
  cachedTimezone = tz
  for (const notify of subscribers) notify(tz)
}

/**
 * Returns the configured timezone from settings (e.g. "America/New_York").
 * Falls back to the browser's local timezone until settings are loaded.
 */
export function useTimezone(): string {
  const [tz, setTz] = useState(cachedTimezone ?? BROWSER_TZ)

  useEffect(() => {
    subscribers.add(setTz)
    if (cachedTimezone) {
      setTz(cachedTimezone)
    } else {
      // Use raw fetch to avoid api.get throwing on non-200 responses
      // (which would break tests that mock fetch globally).
      fetch("/api/settings", { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data?.general?.timezone) setConfiguredTimezone(data.general.timezone)
        })
        .catch(() => {})
    }
    return () => {
      subscribers.delete(setTz)
    }
  }, [])

  return tz
}

/**
 * Parse a backend timestamp string into a Date.
 *
 * Backend `created_at`/`updated_at`/event timestamps come from SQLite
 * `func.now()` and serialize via `datetime.isoformat()` with NO timezone
 * designator (e.g. "2026-06-21T12:00:00") even though they represent UTC.
 * JavaScript parses such date-time strings as *local* time, so a later
 * `toLocaleString({ timeZone })` re-projects them into the configured zone —
 * a double offset that shows the wrong time on non-UTC browsers. Strings that
 * already carry a designator ("...Z" or "...+00:00") parse correctly and must
 * be left untouched. Normalize only the naive case to UTC.
 * @internal exported for tests
 */
export function parseBackendDate(value: string): Date {
  const tIndex = value.indexOf("T")
  if (tIndex !== -1) {
    const timePart = value.slice(tIndex + 1)
    const hasDesignator = /[zZ]$/.test(value) || /[+-]\d\d(?::?\d\d)?$/.test(timePart)
    if (!hasDesignator) return new Date(`${value}Z`)
  }
  return new Date(value)
}

/** Format a date string or Date using the configured timezone. */
export function formatDateTime(
  date: string | Date | null | undefined,
  timezone: string,
  options?: Intl.DateTimeFormatOptions,
): string {
  if (!date) return ""
  const d = typeof date === "string" ? parseBackendDate(date) : date
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
