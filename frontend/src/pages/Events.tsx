import { Fragment, useCallback, useEffect, useState } from "react"
import { api, type EventItem, type EventsResponse } from "@/lib/api"
import { useTimezone, formatDateTimeOrDash } from "@/lib/useTimezone"
import { Loader2, AlertCircle, Info, AlertTriangle, Search, ChevronDown, ChevronRight } from "lucide-react"
import { cn, errorMessage } from "@/lib/utils"
import { eventLevelStyle } from "@/lib/statusStyles"
import { useResource } from "@/lib/useResource"
import { usePagination } from "@/lib/usePagination"
import { Pagination } from "@/components/Pagination"

const SEARCH_DEBOUNCE_MS = 300

const LEVEL_ICONS: Record<string, typeof Info> = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
}

export default function Events() {
  const [search, setSearch] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [levelFilter, setLevelFilter] = useState("")
  const [kindFilter, setKindFilter] = useState("")
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const { offset, limit, total, page, pageCount, setOffset, setTotal, prev, next, goToPage, clampToContent } =
    usePagination()

  const fetcher = useCallback(
    () => api.events.list({ search, level: levelFilter, kind: kindFilter, limit, offset }),
    [search, levelFilter, kindFilter, limit, offset],
  )
  // Sync `total` for the pager and clamp the offset when the current page fell
  // off the end. Events never used to clamp, so the new retention cleanup could
  // shrink `total` and strand the user on an empty page over still-reachable
  // rows; the shared helper fixes that. Returning `true` keeps the spinner up
  // and skips storing the empty page so the offset change retriggers the load.
  const onData = useCallback(
    (data: EventsResponse): boolean => {
      setTotal(data.total)
      const clamped = clampToContent(data.total, data.events.length)
      if (clamped !== null) {
        setOffset(clamped)
        return true
      }
      return false
    },
    [setTotal, clampToContent, setOffset],
  )
  const { data, loading, error } = useResource(fetcher, {
    onData,
    mapError: (e) => (errorMessage(e, "Failed to load events")),
  })
  const events = data?.events ?? []

  // Kind filter options come from the backend registry (GET /events/kinds) —
  // the single source of truth — rather than a hardcoded mirror that silently
  // drifts when a new event kind is added on the backend.
  const kindsFetcher = useCallback(() => api.events.kinds(), [])
  const { data: kindsData } = useResource(kindsFetcher)
  const eventKinds = kindsData?.kinds ?? []

  // Debounce the free-text search so a settled query issues a single request
  // instead of one per keystroke. Level/kind filters and pagination stay
  // immediate; useResource's request-id guard still drops stale responses.
  useEffect(() => {
    const handle = setTimeout(() => {
      setSearch(searchInput)
      setOffset(0)
    }, SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [searchInput, setOffset])

  const tz = useTimezone()

  const toggleEventDetails = (evt: EventItem) => {
    if (!evt.details) return
    setExpandedId((current) => current === evt.id ? null : evt.id)
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
            aria-label="Search event messages"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="pl-9 pr-3 py-2 border rounded-md text-sm w-64"
          />
        </div>
        <select
          aria-label="Filter by level"
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
          aria-label="Filter by kind"
          value={kindFilter}
          onChange={(e) => { setKindFilter(e.target.value); setOffset(0) }}
          className="border rounded-md px-3 py-2 text-sm"
        >
          <option value="">All kinds</option>
          {eventKinds.map((k) => (
            <option key={k} value={k}>{k}</option>
          ))}
        </select>
        <span className="text-sm text-zinc-500">{total} event{total !== 1 ? "s" : ""}</span>
      </div>

      {/* Content */}
      {loading ? (
        <div className="mt-8 flex items-center gap-2 text-zinc-500">
          <Loader2 className="h-5 w-5 animate-spin" /> Loading events...
        </div>
      ) : error ? (
        <div role="alert" className="mt-4 rounded-md bg-red-50 p-4 text-red-700">{error}</div>
      ) : events.length === 0 ? (
        <div className="mt-8 text-zinc-500">No events found.</div>
      ) : (
        <>
          <div className="mt-4 border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 text-left text-zinc-600">
                <tr>
                  <th scope="col" className="px-3 py-2 w-8"><span className="sr-only">Details</span></th>
                  <th scope="col" className="px-3 py-2">Time</th>
                  <th scope="col" className="px-3 py-2">Level</th>
                  <th scope="col" className="px-3 py-2">Kind</th>
                  <th scope="col" className="px-3 py-2">Message</th>
                </tr>
              </thead>
              <tbody>
                {events.map((evt) => {
                  const Icon = LEVEL_ICONS[evt.level] || Info
                  const expanded = expandedId === evt.id
                  return (
                    <Fragment key={evt.id}>
                      <tr
                        className={cn("border-t hover:bg-zinc-50", evt.details && "cursor-pointer")}
                        onClick={() => toggleEventDetails(evt)}
                      >
                        <td className="px-3 py-2">
                          {evt.details ? (
                            <button
                              type="button"
                              aria-label={`${expanded ? "Collapse" : "Expand"} details for ${evt.message}`}
                              aria-expanded={expanded}
                              aria-controls={`event-details-${evt.id}`}
                              onClick={(e) => {
                                e.stopPropagation()
                                toggleEventDetails(evt)
                              }}
                              className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 focus:outline-none focus:ring-2 focus:ring-zinc-500"
                            >
                              {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                            </button>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 whitespace-nowrap text-zinc-500 font-mono text-xs">
                          {formatDateTimeOrDash(evt.created_at, tz)}
                        </td>
                        <td className="px-3 py-2">
                          <span className={cn("inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium", eventLevelStyle(evt.level))}>
                            <Icon className="h-3 w-3" />
                            {evt.level}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-zinc-600">{evt.kind}</td>
                        <td className="px-3 py-2">{evt.message}</td>
                      </tr>
                      {expanded && evt.details && (
                        <tr id={`event-details-${evt.id}`} className="border-t bg-zinc-50">
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

          <Pagination
            offset={offset}
            limit={limit}
            total={total}
            page={page}
            pageCount={pageCount}
            onPrev={prev}
            onNext={next}
            onGoToPage={goToPage}
          />
        </>
      )}
    </div>
  )
}
