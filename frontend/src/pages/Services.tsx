import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { api, type ServiceItem, type ServiceListResponse } from "@/lib/api"
import { useTimezone, formatDate } from "@/lib/useTimezone"
import { cn } from "@/lib/utils"
import { Loader2, Plus, ExternalLink, MoreVertical } from "lucide-react"

const PHASE_STYLES: Record<string, string> = {
  healthy: "bg-green-100 text-green-700",
  pending: "bg-yellow-100 text-yellow-700",
  warning: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
  failed: "bg-red-100 text-red-700",
}

function formatCertExpiry(iso: string | null | undefined, tz: string): { text: string; style: string } {
  if (!iso) return { text: "—", style: "text-zinc-400" }
  const expiry = new Date(iso)
  const now = new Date()
  const daysLeft = Math.ceil((expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
  const text = formatDate(iso, tz)
  if (daysLeft < 0) return { text, style: "text-red-600 font-medium" }
  if (daysLeft <= 14) return { text, style: "text-yellow-600 font-medium" }
  return { text, style: "text-zinc-500" }
}

export default function Services() {
  const navigate = useNavigate()
  const tz = useTimezone()
  const [services, setServices] = useState<ServiceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    api.get<ServiceListResponse>("/services")
      .then((data) => setServices(data.services))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const doAction = async (svcId: string, path: string, method: "post" | "put" = "post", body?: unknown) => {
    setOpenMenuId(null)
    setMenuPos(null)
    try {
      if (method === "put") {
        await api.put(`/services/${svcId}${path}`, body)
      } else {
        await api.post(`/services/${svcId}${path}`)
      }
      load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setActionMsg(msg)
      setTimeout(() => setActionMsg(null), 3000)
    }
  }

  const handleDelete = async (svc: ServiceItem) => {
    setOpenMenuId(null)
    setMenuPos(null)
    if (!window.confirm(`Delete service "${svc.name}"? This cannot be undone.`)) return
    try {
      await api.delete(`/services/${svc.id}`)
      load()
    } catch (e) {
      setActionMsg(e instanceof Error ? e.message : String(e))
      setTimeout(() => setActionMsg(null), 3000)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading services...
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Services</h1>
          <p className="mt-1 text-sm text-zinc-500">Manage your exposed services.</p>
        </div>
        <button
          onClick={() => navigate("/discover")}
          className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800"
        >
          <Plus className="h-4 w-4" />
          Expose New Service
        </button>
      </div>

      {actionMsg && (
        <div className="mt-4 rounded-md bg-yellow-50 px-4 py-2 text-sm text-yellow-800">{actionMsg}</div>
      )}

      {services.length === 0 ? (
        <div className="mt-8 rounded-md bg-zinc-50 px-4 py-12 text-center">
          <p className="text-sm text-zinc-500">No services exposed yet.</p>
          <button
            onClick={() => navigate("/discover")}
            className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800"
          >
            <Plus className="h-4 w-4" />
            Discover Containers
          </button>
        </div>
      ) : (
        <div className="mt-6 overflow-x-auto rounded-md border border-zinc-200">
          <table className="min-w-full divide-y divide-zinc-200">
            <thead className="bg-zinc-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Service</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Hostname</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Upstream</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Edge IP</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Cert Expiry</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase text-zinc-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 bg-white">
              {services.map((svc) => {
                const phase = svc.status?.phase || "pending"
                const cert = formatCertExpiry(svc.status?.cert_expires_at, tz)
                return (
                  <tr key={svc.id} className="hover:bg-zinc-50">
                    <td className="whitespace-nowrap px-4 py-3">
                      <Link to={`/services/${svc.id}`} className="text-sm font-medium text-zinc-900 hover:underline">
                        {svc.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-500">
                      <span className="inline-flex items-center gap-1">
                        {svc.hostname}
                        <ExternalLink className="h-3 w-3" />
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-500">
                      {svc.upstream_container_name}:{svc.upstream_port}
                    </td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                        PHASE_STYLES[phase] || "bg-zinc-100 text-zinc-600"
                      )}>
                        {phase}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-zinc-500 font-mono">
                      {svc.status?.tailscale_ip || "—"}
                    </td>
                    <td className={cn("px-4 py-3 text-sm", cert.style)}>
                      {cert.text}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-block">
                        <button
                          onClick={(e) => {
                            if (openMenuId === svc.id) {
                              setOpenMenuId(null)
                              setMenuPos(null)
                            } else {
                              const rect = e.currentTarget.getBoundingClientRect()
                              setMenuPos({ top: rect.bottom + 4, left: rect.right - 176 })
                              setOpenMenuId(svc.id)
                            }
                          }}
                          className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
                          aria-label="Actions"
                        >
                          <MoreVertical className="h-4 w-4" />
                        </button>
                        {openMenuId === svc.id && menuPos && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => { setOpenMenuId(null); setMenuPos(null) }} />
                            <div
                              className="fixed z-50 w-44 rounded-md border border-zinc-200 bg-white py-1 shadow-lg"
                              style={{ top: menuPos.top, left: menuPos.left }}
                            >
                              <Link to={`/services/${svc.id}`} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                View Details
                              </Link>
                              <button onClick={() => doAction(svc.id, "/reload")} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                Reload Caddy
                              </button>
                              <button onClick={() => doAction(svc.id, "/restart-edge")} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                Restart Edge
                              </button>
                              <button onClick={() => doAction(svc.id, "/recreate-edge")} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                Recreate Edge
                              </button>
                              {svc.enabled ? (
                                <button onClick={() => doAction(svc.id, "/disable")} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                  Disable
                                </button>
                              ) : (
                                <button onClick={() => doAction(svc.id, "", "put", { enabled: true })} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                  Enable
                                </button>
                              )}
                              <button onClick={() => handleDelete(svc)} className="block w-full px-3 py-1.5 text-left text-sm text-red-600 hover:bg-red-50">
                                Delete
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
