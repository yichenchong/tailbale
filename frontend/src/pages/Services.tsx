import { useCallback, useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { api, type ServiceItem } from "@/lib/api"
import { useTimezone } from "@/lib/useTimezone"
import { cn } from "@/lib/utils"
import { formatCertExpiry } from "@/lib/certStatus"
import { phaseStyle } from "@/lib/statusStyles"
import { useResource } from "@/lib/useResource"
import { Loader2, Plus, ExternalLink, MoreVertical } from "lucide-react"

export default function Services() {
  const navigate = useNavigate()
  const tz = useTimezone()
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const actionMsgTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetcher = useCallback(() => api.services.list(), [])
  const { data, loading, error, refresh } = useResource(fetcher)
  const services = data?.services ?? []

  const showActionMsg = useCallback((msg: string) => {
    if (actionMsgTimerRef.current !== null) clearTimeout(actionMsgTimerRef.current)
    setActionMsg(msg)
    actionMsgTimerRef.current = setTimeout(() => {
      setActionMsg(null)
      actionMsgTimerRef.current = null
    }, 3000)
  }, [])

  useEffect(() => {
    return () => {
      if (actionMsgTimerRef.current !== null) clearTimeout(actionMsgTimerRef.current)
    }
  }, [])

  const runAction = async (action: () => Promise<unknown>) => {
    setOpenMenuId(null)
    setMenuPos(null)
    try {
      await action()
      void refresh()
    } catch (e) {
      showActionMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const handleDelete = async (svc: ServiceItem) => {
    setOpenMenuId(null)
    setMenuPos(null)
    if (!window.confirm(`Delete service "${svc.name}"? This also removes its DNS record and cannot be undone.`)) return
    try {
      // Match the detail page's default: clean up the Cloudflare DNS record so a
      // list-delete doesn't silently orphan a record pointing at a now-dead IP.
      await api.services.remove(svc.id, { cleanupDns: true })
      void refresh()
    } catch (e) {
      showActionMsg(e instanceof Error ? e.message : String(e))
    }
  }

  if (loading && services.length === 0) {
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

      {error && services.length > 0 && (
        <div className="mt-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-800">
          Unable to refresh services: {error}
        </div>
      )}

      {error && services.length === 0 ? (
        <div className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          Unable to load services: {error}
        </div>
      ) : services.length === 0 ? (
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
                      <Link to={`/services/${encodeURIComponent(svc.id)}`} className="text-sm font-medium text-zinc-900 hover:underline">
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
                        phaseStyle(phase)
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
                              <Link to={`/services/${encodeURIComponent(svc.id)}`} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                View Details
                              </Link>
                              {svc.enabled && (
                                <>
                                  <button onClick={() => runAction(() => api.services.reload(svc.id))} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                    Reload Caddy
                                  </button>
                                  <button onClick={() => runAction(() => api.services.restartEdge(svc.id))} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                    Restart Edge
                                  </button>
                                  <button onClick={() => runAction(() => api.services.recreateEdge(svc.id))} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                    Recreate Edge
                                  </button>
                                </>
                              )}
                              {svc.enabled ? (
                                <button onClick={() => runAction(() => api.services.disable(svc.id))} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
                                  Disable
                                </button>
                              ) : (
                                <button onClick={() => runAction(() => api.services.update(svc.id, { enabled: true }))} className="block w-full px-3 py-1.5 text-left text-sm text-zinc-700 hover:bg-zinc-50">
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
