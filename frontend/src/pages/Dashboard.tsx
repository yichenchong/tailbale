import { useCallback } from "react"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { useTimezone, formatDateTime } from "@/lib/useTimezone"
import { cn, errorMessage } from "@/lib/utils"
import { certStatus } from "@/lib/certStatus"
import { eventLevelStyle } from "@/lib/statusStyles"
import { useResource } from "@/lib/useResource"
import { PolledRefreshControl, StaleDataBanner } from "@/lib/polledFreshness"
import { usePolledFreshness } from "@/lib/usePolledFreshness"
import {
  Loader2,
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ShieldAlert,
  Clock,
} from "lucide-react"

const POLL_INTERVAL = 30_000

export default function Dashboard() {
  const tz = useTimezone()
  const { lastRefresh, markFresh } = usePolledFreshness()

  const fetcher = useCallback(() => api.dashboard.summary(), [])
  const { data, loading, error, refresh } = useResource(fetcher, {
    pollMs: POLL_INTERVAL,
    mapError: (e) => (errorMessage(e, "Failed to load")),
    onData: markFresh,
  })

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading dashboard...
      </div>
    )
  }

  if (error && !data) {
    return <div role="alert" className="rounded-md bg-red-50 p-4 text-red-700">{error}</div>
  }

  if (!data) return null

  const s = data.services

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="mt-1 text-zinc-500">Overview of your exposed services.</p>
        </div>
        <PolledRefreshControl
          lastRefresh={lastRefresh}
          timezone={tz}
          loading={loading}
          onRefresh={() => { void refresh() }}
        />
      </div>

      <StaleDataBanner error={error} lastRefresh={lastRefresh} timezone={tz} />

      {/* Summary cards */}
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card icon={Activity} label="Total Services" value={s.total} color="text-zinc-700" bg="bg-zinc-50" />
        <Card icon={CheckCircle2} label="Healthy" value={s.healthy} color="text-green-700" bg="bg-green-50" />
        <Card icon={AlertTriangle} label="Warning" value={s.warning} color="text-yellow-700" bg="bg-yellow-50" />
        <Card icon={XCircle} label="Error" value={s.error} color="text-red-700" bg="bg-red-50" />
      </div>

      <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Expiring certs */}
        <section className="rounded-md border border-zinc-200 p-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-700">
            <ShieldAlert className="h-4 w-4" /> Upcoming Cert Expiries
          </h2>
          {data.expiring_certs.length === 0 ? (
            <p className="mt-3 text-sm text-zinc-400">No certificates approaching expiry.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.expiring_certs.map((c) => {
                const cert = certStatus(c.expires_at)
                return (
                  <li key={c.service_id} className="flex items-center justify-between text-sm">
                    <Link to={`/services/${encodeURIComponent(c.service_id)}`} className="text-zinc-700 hover:underline">
                      {c.service_name} <span className="text-zinc-400">({c.hostname})</span>
                    </Link>
                    <span className={cn("font-medium", cert.color)}>
                      {cert.label}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {/* Recent errors */}
        <section className="rounded-md border border-zinc-200 p-4">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-700">
            <XCircle className="h-4 w-4 text-red-500" /> Recent Errors
          </h2>
          {data.recent_errors.length === 0 ? (
            <p className="mt-3 text-sm text-zinc-400">No recent errors.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.recent_errors.slice(0, 8).map((e) => (
                <li key={e.id} className="text-sm">
                  <span className="font-mono text-xs text-zinc-400">
                    {e.created_at ? formatDateTime(e.created_at, tz) : ""}
                  </span>
                  <p className="text-zinc-700">{e.message}</p>
                </li>
              ))}
            </ul>
          )}
          {data.recent_errors.length > 8 && (
            <Link to="/events" className="mt-3 inline-block text-sm text-zinc-500 hover:text-zinc-700 hover:underline">
              View all events
            </Link>
          )}
        </section>
      </div>

      {/* Recent events timeline */}
      <section className="mt-6 rounded-md border border-zinc-200 p-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-700">
          <Clock className="h-4 w-4" /> Recent Events
        </h2>
        {data.recent_events.length === 0 ? (
          <p className="mt-3 text-sm text-zinc-400">No events yet.</p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {data.recent_events.slice(0, 10).map((e) => (
              <li key={e.id} className="flex items-start gap-3 text-sm">
                <span className="w-36 shrink-0 font-mono text-xs text-zinc-400">
                  {e.created_at ? formatDateTime(e.created_at, tz) : ""}
                </span>
                <span className={cn(
                  "inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                  eventLevelStyle(e.level)
                )}>
                  {e.level}
                </span>
                <span className="text-zinc-700">{e.message}</span>
              </li>
            ))}
          </ul>
        )}
        {data.recent_events.length > 10 && (
          <Link to="/events" className="mt-3 inline-block text-sm text-zinc-500 hover:text-zinc-700 hover:underline">
            View all events
          </Link>
        )}
      </section>
    </div>
  )
}

function Card({ icon: Icon, label, value, color, bg }: {
  icon: typeof Activity
  label: string
  value: number
  color: string
  bg: string
}) {
  return (
    <div className={cn("rounded-lg border border-zinc-200 p-4", bg)}>
      <div className="flex items-center gap-2">
        <Icon className={cn("h-5 w-5", color)} />
        <span className="text-sm font-medium text-zinc-500">{label}</span>
      </div>
      <p className={cn("mt-2 text-3xl font-bold", color)}>{value}</p>
    </div>
  )
}
