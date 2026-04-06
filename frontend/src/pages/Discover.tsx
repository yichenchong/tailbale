import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { api, type DiscoveredContainer, type DiscoveryResponse, type ServiceListResponse } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Loader2, Search, Globe } from "lucide-react"

export default function Discover() {
  const navigate = useNavigate()
  const [containers, setContainers] = useState<DiscoveredContainer[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [runningOnly, setRunningOnly] = useState(true)
  // Track how many exposures each container already has
  const [exposureCounts, setExposureCounts] = useState<Record<string, number>>({})

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [discovery, services] = await Promise.all([
        api.get<DiscoveryResponse>(`/discovery/containers?${new URLSearchParams({
          running_only: String(runningOnly),
          hide_managed: "true",
          ...(search ? { search } : {}),
        })}`),
        api.get<ServiceListResponse>("/services"),
      ])
      setContainers(discovery.containers)
      const counts: Record<string, number> = {}
      for (const svc of services.services) {
        counts[svc.upstream_container_id] = (counts[svc.upstream_container_id] || 0) + 1
      }
      setExposureCounts(counts)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [runningOnly])

  const handleSearch = () => load()

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
      <h1 className="text-2xl font-bold">Discover Containers</h1>
      <p className="mt-1 text-sm text-zinc-500">Find running Docker containers to expose as HTTPS services.</p>

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
        {loading ? (
          <div className="flex items-center gap-2 py-8 text-zinc-500">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading containers...
          </div>
        ) : error ? (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        ) : containers.length === 0 ? (
          <div className="rounded-md bg-zinc-50 px-4 py-8 text-center text-sm text-zinc-500">
            No containers found. Make sure Docker is accessible and containers are running.
          </div>
        ) : (
          <div className="overflow-hidden rounded-md border border-zinc-200">
            <table className="min-w-full divide-y divide-zinc-200">
              <thead className="bg-zinc-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Image</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Status</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Ports</th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Networks</th>
                  <th className="px-4 py-3 text-right text-xs font-medium uppercase text-zinc-500">Action</th>
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
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-500">
                      {c.networks.length > 0 ? c.networks.join(", ") : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex items-center gap-2">
                        {exposureCounts[c.id] ? (
                          <span className="inline-flex items-center rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                            {exposureCounts[c.id]} exposed
                          </span>
                        ) : null}
                        <button
                          onClick={() => handleExpose(c)}
                          className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800"
                        >
                          <Globe className="h-3 w-3" />
                          {exposureCounts[c.id] ? "Expose Another Port" : "Expose"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
