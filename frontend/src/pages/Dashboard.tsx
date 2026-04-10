import { useCallback, useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { useTimezone, formatDateTime, formatTime as fmtTime } from "@/lib/useTimezone"
import { cn } from "@/lib/utils"
import {
  Loader2,
  Activity,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  ShieldAlert,
  Clock,
  RefreshCw,
} from "lucide-react"

const POLL_INTERVAL = 30_000

interface DashboardSummary {
  services: {
    total: number
    healthy: number
    warning: number
    error: number
  }
  expiring_certs: {
    service_id: string
    service_name: string
    hostname: string
    expires_at: string | null
  }[]
  recent_errors: {
    id: string
    service_id: string | null
    kind: string
    message: string
    created_at: string | null
  }[]
  recent_events: {
    id: string
    service_id: string | null
    kind: string
    level: string
    message: string
    created_at: string | null
  }[]
}

export default function Dashboard() {
  const tz = useTimezone()
  const [data, setData] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true)
    try {
      const result = await api.get<DashboardSummary>("/dashboard/summary")
      setData(result)
      setError("")
      setLastRefresh(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(true)
  }, [load])

  // Auto-refresh every 30s
  useEffect(() => {
    timerRef.current = setInterval(() => load(false), POLL_INTERVAL)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [load])

  if (loading && !data) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading dashboard...
      </div>
    )
  }

  if (error && !data) {
    return <div className="rounded-md bg-red-50 p-4 text-red-700">{error}</div>
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
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-zinc-400">
              Updated {fmtTime(lastRefresh, tz)}
            </span>
          )}
          <button
            onClick={() => load(true)}
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
      </div>

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
            <p className="mt-3 text-sm text-zinc-400">No certificates expiring within 30 days.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {data.expiring_certs.map((c) => {
                const days = c.expires_at
                  ? Math.ceil((new Date(c.expires_at).getTime() - Date.now()) / 86400000)
                  : null
                return (
                  <li key={c.service_id} className="flex items-center justify-between text-sm">
                    <Link to={`/services/${c.service_id}`} className="text-zinc-700 hover:underline">
                      {c.service_name} <span className="text-zinc-400">({c.hostname})</span>
                    </Link>
                    <span className={cn(
                      "font-medium",
                      days !== null && days < 0 ? "text-red-600" : days !== null && days <= 7 ? "text-yellow-600" : "text-zinc-500"
                    )}>
                      {days !== null ? (days < 0 ? "Expired" : `${days}d left`) : "\u2014"}
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
                  e.level === "error" ? "bg-red-100 text-red-700" : e.level === "warning" ? "bg-yellow-100 text-yellow-700" : "bg-blue-100 text-blue-700"
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
