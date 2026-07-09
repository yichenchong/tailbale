import { useCallback, useState } from "react"
import { useNavigate } from "react-router-dom"
import { api, type DiscoveredContainer } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Loader2, Search, Globe } from "lucide-react"
import { useTimezone } from "@/lib/useTimezone"
import { useResource } from "@/lib/useResource"
import { PolledRefreshControl, StaleDataBanner } from "@/lib/polledFreshness"
import { usePolledFreshness } from "@/lib/usePolledFreshness"

const POLL_INTERVAL = 30_000 // 30 seconds

interface DiscoverData {
  containers: DiscoveredContainer[]
  // How many exposures (services) each container already has, keyed by container id.
  exposureCounts: Record<string, number>
}

export default function Discover() {
  const navigate = useNavigate()
  const tz = useTimezone()
  const [search, setSearch] = useState("")
  const [appliedSearch, setAppliedSearch] = useState("")
  const [runningOnly, setRunningOnly] = useState(true)
  const { lastRefresh, markFresh } = usePolledFreshness()

  // Composite fetcher: merge the discovery + services endpoints and derive each
  // container's exposure count. Memoized over its inputs so a filter/search
  // change re-runs the load (mirrors the old useEffect(load, [load]) pattern).
  const fetcher = useCallback(async (): Promise<DiscoverData> => {
    const [discovery, services] = await Promise.all([
      api.discovery.containers({ runningOnly, search: appliedSearch }),
      api.services.list(),
    ])
    const exposureCounts: Record<string, number> = {}
    for (const svc of services.services) {
      if (svc.upstream_container_id) {
        exposureCounts[svc.upstream_container_id] = (exposureCounts[svc.upstream_container_id] || 0) + 1
      }
    }
    return { containers: discovery.containers, exposureCounts }
  }, [runningOnly, appliedSearch])

  // Shared fetch/loading/error/race-guard/poll machine. The background poll keeps
  // the current list visible and clears the error only on success (no flicker);
  // the 30s cadence matches the old hand-rolled setInterval.
  const { data, loading, error, refresh } = useResource(fetcher, {
    pollMs: POLL_INTERVAL,
    onData: markFresh,
  })
  const containers = data?.containers ?? []
  const exposureCounts = data?.exposureCounts ?? {}

  const handleSearch = () => {
    if (search === appliedSearch) {
      void refresh()
    } else {
      setAppliedSearch(search)
    }
  }

  const handleExpose = (container: DiscoveredContainer) => {
    const params = new URLSearchParams({
      container_id: container.id,
      container_name: container.name,
      image: container.image,
      ports: JSON.stringify(container.ports),
    })
    navigate(`/expose?${params}`)
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Discover Containers</h1>
          <p className="mt-1 text-sm text-zinc-500">Find running Docker containers to expose as HTTPS services.</p>
        </div>
        <PolledRefreshControl
          lastRefresh={lastRefresh}
          timezone={tz}
          loading={loading}
          onRefresh={() => { void refresh() }}
        />
      </div>

      {/* Filters */}
      <div className="mt-6 flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Search by name or image..."
            aria-label="Search containers by name or image"
            className="w-full rounded-md border border-zinc-300 py-2 pl-9 pr-3 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </div>
        <button
          onClick={handleSearch}
          className="rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800"
        >
          Search
        </button>
        <label className="flex items-center gap-2 text-sm text-zinc-600">
          <input
            type="checkbox"
            checked={runningOnly}
            onChange={(e) => setRunningOnly(e.target.checked)}
            className="rounded border-zinc-300"
          />
          Running only
        </label>
      </div>

      {/* Content */}
      <div className="mt-4">
        {loading && containers.length === 0 ? (
          <div className="flex items-center gap-2 py-8 text-zinc-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading containers...
          </div>
        ) : error && containers.length === 0 ? (
          <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        ) : containers.length === 0 ? (
          <div className="rounded-md bg-zinc-50 px-4 py-8 text-center text-sm text-zinc-500">
            No containers found. Make sure Docker is accessible and containers are running.
          </div>
        ) : (
          <>
            <StaleDataBanner error={error} lastRefresh={lastRefresh} timezone={tz} className="mb-3" />
            <div className="overflow-x-auto rounded-md border border-zinc-200">
              <table className="min-w-full divide-y divide-zinc-200">
                <thead className="bg-zinc-50">
                  <tr>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Name</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Image</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Status</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Ports</th>
                    <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Networks</th>
                    <th scope="col" className="px-4 py-3 text-right text-xs font-medium uppercase text-zinc-500">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 bg-white">
                  {containers.map((c) => (
                    <tr key={c.id} className="hover:bg-zinc-50">
                      <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-zinc-900">{c.name}</td>
                      <td className="px-4 py-3 text-sm text-zinc-500 max-w-[200px] truncate" title={c.image}>{c.image}</td>
                      <td className="px-4 py-3">
                        <span className={cn(
                          "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                          c.state === "running" ? "bg-green-100 text-green-700" : "bg-zinc-100 text-zinc-600"
                        )}>
                          {c.state}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-zinc-500">
                        {c.ports.length > 0
                          ? c.ports.map((p) => `${p.container_port}/${p.protocol}`).join(", ")
                          : "\u2014"}
                      </td>
                      <td className="px-4 py-3 text-sm text-zinc-500">
                        {c.networks.length > 0 ? c.networks.join(", ") : "\u2014"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-right">
                        <div className="inline-flex items-center gap-2">
                          {exposureCounts[c.id] ? (
                            <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                              {exposureCounts[c.id]} svc
                            </span>
                          ) : null}
                          <button
                            onClick={() => handleExpose(c)}
                            className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800"
                          >
                            <Globe className="hidden h-3 w-3 sm:inline" />
                            Expose
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
