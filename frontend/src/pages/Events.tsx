import { Fragment, useEffect, useState } from "react"
import { api } from "@/lib/api"
import { useTimezone, formatDateTime } from "@/lib/useTimezone"
import { Loader2, AlertCircle, Info, AlertTriangle, Search, ChevronDown, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"

interface EventItem {
  id: string
  service_id: string | null
  kind: string
  level: string
  message: string
  details: Record<string, unknown> | null
  created_at: string | null
}

interface EventsResponse {
  events: EventItem[]
  total: number
}

const LEVEL_STYLES: Record<string, string> = {
  info: "bg-blue-100 text-blue-700",
  warning: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-700",
}

const LEVEL_ICONS: Record<string, typeof Info> = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
}

export default function Events() {
  const [events, setEvents] = useState<EventItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [search, setSearch] = useState("")
  const [levelFilter, setLevelFilter] = useState("")
  const [kindFilter, setKindFilter] = useState("")
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const limit = 50

  async function load() {
    setLoading(true)
    setError("")
    try {
      const params = new URLSearchParams()
      if (search) params.set("search", search)
      if (levelFilter) params.set("level", levelFilter)
      if (kindFilter) params.set("kind", kindFilter)
      params.set("limit", String(limit))
      params.set("offset", String(offset))
      const qs = params.toString()
      const data = await api.get<EventsResponse>(`/events${qs ? `?${qs}` : ""}`)
      setEvents(data.events)
      setTotal(data.total)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load events")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [search, levelFilter, kindFilter, offset])

  const tz = useTimezone()
  function fmtTime(iso: string | null) {
    if (!iso) return "—"
    return formatDateTime(iso, tz)
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Events</h1>
      <p className="mt-1 text-zinc-500">Activity log and event history.</p>

      {/* Filters */}
      <div className="mt-4 flex flex-wrap gap-3 items-center">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-zinc-400" />
          <input
            type="text"
            placeholder="Search messages..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setOffset(0) }}
            className="pl-9 pr-3 py-2 border rounded-md text-sm w-64"
          />
        </div>
        <select
          value={levelFilter}
          onChange={(e) => { setLevelFilter(e.target.value); setOffset(0) }}
          className="border rounded-md px-3 py-2 text-sm"
        >
          <option value="">All levels</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
        </select>
        <select
          value={kindFilter}
          onChange={(e) => { setKindFilter(e.target.value); setOffset(0) }}
          className="border rounded-md px-3 py-2 text-sm"
        >
          <option value="">All kinds</option>
          <option value="service_created">service_created</option>
          <option value="service_updated">service_updated</option>
          <option value="service_deleted">service_deleted</option>
          <option value="edge_started">edge_started</option>
          <option value="edge_restarted">edge_restarted</option>
          <option value="edge_recreated">edge_recreated</option>
          <option value="caddy_reloaded">caddy_reloaded</option>
          <option value="tailscale_ip_acquired">tailscale_ip_acquired</option>
          <option value="cert_issued">cert_issued</option>
          <option value="cert_renewed">cert_renewed</option>
          <option value="cert_failed">cert_failed</option>
          <option value="dns_created">dns_created</option>
          <option value="dns_updated">dns_updated</option>
          <option value="dns_removed">dns_removed</option>
          <option value="reconcile_completed">reconcile_completed</option>
          <option value="reconcile_failed">reconcile_failed</option>
        </select>
        <span className="text-sm text-zinc-500">{total} events</span>
      </div>

      {/* Content */}
      {loading ? (
        <div className="mt-8 flex items-center gap-2 text-zinc-500">
          <Loader2 className="h-5 w-5 animate-spin" /> Loading events...
        </div>
      ) : error ? (
        <div className="mt-4 rounded-md bg-red-50 p-4 text-red-700">{error}</div>
      ) : events.length === 0 ? (
        <div className="mt-8 text-zinc-500">No events found.</div>
      ) : (
        <>
          <div className="mt-4 border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-zinc-600">
                <tr>
                  <th className="px-3 py-2 w-8"></th>
                  <th className="px-3 py-2">Time</th>
                  <th className="px-3 py-2">Level</th>
                  <th className="px-3 py-2">Kind</th>
                  <th className="px-3 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {events.map((evt) => {
                  const Icon = LEVEL_ICONS[evt.level] || Info
                  const expanded = expandedId === evt.id
                  return (
                    <Fragment key={evt.id}>
                      <tr
                        className="border-t hover:bg-zinc-50 cursor-pointer"
                        onClick={() => setExpandedId(expanded ? null : evt.id)}
                      >
                        <td className="px-3 py-2">
                          {evt.details ? (
                            expanded ? <ChevronDown className="h-4 w-4 text-zinc-400" /> : <ChevronRight className="h-4 w-4 text-zinc-400" />
                          ) : null}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-zinc-500 font-mono text-xs">
                          {fmtTime(evt.created_at)}
                        </td>
                        <td className="px-3 py-2">
                          <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", LEVEL_STYLES[evt.level] || "bg-zinc-100 text-zinc-600")}>
                            <Icon className="h-3 w-3" />
                            {evt.level}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-zinc-600">{evt.kind}</td>
                        <td className="px-3 py-2">{evt.message}</td>
                      </tr>
                      {expanded && evt.details && (
                        <tr className="border-t bg-zinc-50">
                          <td colSpan={5} className="px-6 py-3">
                            <pre className="text-xs text-zinc-700 whitespace-pre-wrap font-mono">
                              {JSON.stringify(evt.details, null, 2)}
                            </pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {total > limit && (
            <div className="mt-3 flex gap-2 items-center text-sm">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Previous
              </button>
              <span className="text-zinc-500">
                {offset + 1}–{Math.min(offset + limit, total)} of {total}
              </span>
              <button
                disabled={offset + limit >= total}
                onClick={() => setOffset(offset + limit)}
                className="px-3 py-1 border rounded disabled:opacity-50"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
