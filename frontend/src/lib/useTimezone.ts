import { useEffect, useSyncExternalStore } from "react"
import { getJsonSafe } from "@/lib/utils"

/** @internal exported for test cleanup */
export let cachedTimezone: string | null = null
const subscribers = new Set<() => void>()
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
  for (const notify of subscribers) notify()
}

function subscribe(onStoreChange: () => void): () => void {
  subscribers.add(onStoreChange)
  return () => subscribers.delete(onStoreChange)
}

function getSnapshot(): string {
  return cachedTimezone ?? BROWSER_TZ
}

/**
 * Returns the configured timezone from settings (e.g. "America/New_York").
 * Falls back to the browser's local timezone until settings are loaded.
 */
export function useTimezone(): string {
  const tz = useSyncExternalStore(subscribe, getSnapshot)

  useEffect(() => {
    if (cachedTimezone) return
    getJsonSafe<{ general?: { timezone?: string } }>("/api/settings")
      .then((data) => {
        // Ignore a late settings response if a timezone has since been
        // established — by an explicit setConfiguredTimezone (e.g. saving
        // the General settings) or a sibling consumer's fetch. Applying it
        // here would clobber the newer value back to a stale one and
        // re-render every mounted consumer with the wrong zone.
        if (!cachedTimezone && data?.general?.timezone) {
          setConfiguredTimezone(data.general.timezone)
        }
      })
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
  try {
    return d.toLocaleString(undefined, { ...options, timeZone: timezone })
  } catch {
    // An invalid IANA timezone makes toLocaleString throw a RangeError, which
    // would white-screen every timestamp during render. Fall back to UTC
    // (always valid); if even that fails, use the raw ISO string. timeZone is
    // applied last so neither the configured zone nor this UTC safety net can
    // be silently overridden by a caller-supplied options.timeZone.
    try {
      return d.toLocaleString(undefined, { ...options, timeZone: "UTC" })
    } catch {
      return d.toISOString()
    }
  }
}

/**
 * Like formatDateTime but renders the em-dash placeholder for a missing OR
 * unparseable value, so table cells show "—" instead of a blank gap when a
 * timestamp is null/absent or a stored string fails to parse. formatDateTime
 * returns "" for both the null and the invalid-date cases (and always returns a
 * non-empty string for a real date, via the UTC/ISO fallbacks), so an empty
 * result here unambiguously means "no displayable value" → sentinel.
 */
export function formatDateTimeOrDash(
  date: string | Date | null | undefined,
  timezone: string,
): string {
  if (!date) return "—"
  const formatted = formatDateTime(date, timezone)
  return formatted === "" ? "—" : formatted
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
