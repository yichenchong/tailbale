import { Loader2, RefreshCw } from "lucide-react"

import { formatTime } from "@/lib/useTimezone"


export function PolledRefreshControl({
  lastRefresh,
  timezone,
  loading,
  onRefresh,
}: {
  lastRefresh: Date | null
  timezone: string
  loading: boolean
  onRefresh: () => void
}) {
  return (
    <div className="flex items-center gap-3">
      {lastRefresh && (
        <span className="text-xs text-zinc-400">
          Updated {formatTime(lastRefresh, timezone)}
        </span>
      )}
      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <RefreshCw className="h-3.5 w-3.5" />
        )}
        Refresh
      </button>
    </div>
  )
}

export function StaleDataBanner({
  error,
  lastRefresh,
  timezone,
  className = "mt-4",
}: {
  error: string | null
  lastRefresh: Date | null
  timezone: string
  className?: string
}) {
  if (!error) return null
  return (
    <div role="status" className={`${className} rounded-md bg-amber-50 px-4 py-2 text-sm text-amber-800`}>
      {lastRefresh
        ? `Couldn't refresh \u2014 showing data from ${formatTime(lastRefresh, timezone)}`
        : "Couldn't refresh \u2014 showing cached data"}
    </div>
  )
}
