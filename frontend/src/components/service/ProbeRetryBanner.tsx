import { useEffect, useState } from "react"
import { Timer } from "lucide-react"
import { formatDateTime, parseBackendDate } from "@/lib/useTimezone"

/**
 * Live countdown to the next scheduled HTTPS probe retry. Ticks once a second so
 * the relative label ("in 42s" / "in 3m") stays fresh; distant retries fall back
 * to an absolute localized timestamp. `retryAt` is a naive-UTC backend timestamp,
 * so it MUST go through `parseBackendDate` (a raw `new Date()` would misread it as
 * local time and skew the delta by the host offset).
 */
export function ProbeRetryBanner({
  retryAt,
  attempt,
  tz,
}: {
  retryAt: string
  attempt: number | null
  tz: string
}) {
  const [, setTick] = useState(0)

  // Re-render every second so the countdown stays live
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(id)
  }, [])

  const retryDate = parseBackendDate(retryAt)
  const diffMs = retryDate.getTime() - Date.now()

  let timeLabel: string
  if (diffMs <= 0) {
    timeLabel = "any moment now"
  } else if (diffMs < 60_000) {
    const secs = Math.ceil(diffMs / 1000)
    timeLabel = `in ${secs}s`
  } else if (diffMs < 3_600_000) {
    const mins = Math.ceil(diffMs / 60_000)
    timeLabel = `in ${mins}m`
  } else {
    timeLabel = formatDateTime(retryAt, tz)
  }

  return (
    <div className="mt-2 flex items-center gap-1.5 rounded-md bg-yellow-50 px-3 py-2 text-xs text-yellow-700">
      <Timer className="h-3.5 w-3.5 flex-shrink-0" />
      <span>
        HTTPS probe retry #{attempt ?? "?"} scheduled {timeLabel}
      </span>
    </div>
  )
}
