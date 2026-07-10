import { useCallback } from "react"
import { Link, useNavigate } from "react-router-dom"
import { api, type ServiceItem } from "@/lib/api"
import { useTimezone } from "@/lib/useTimezone"
import { cn, errorMessage } from "@/lib/utils"
import { formatCertExpiry } from "@/lib/certStatus"
import { phaseStyle } from "@/lib/statusStyles"
import { useResource } from "@/lib/useResource"
import { useTransientMessage } from "@/lib/useTransientMessage"
import { PageError, PageLoading } from "@/components/PageState"
import { Plus, ExternalLink } from "lucide-react"
import { RowActionsMenu } from "@/components/service/RowActionsMenu"
import { useRowActionMenu, type RowActionMenuItem } from "@/components/service/useRowActionMenu"
import { serviceLifecycleActions } from "@/components/service/lifecycleActions"

export default function Services() {
  const navigate = useNavigate()
  const tz = useTimezone()
  const menu = useRowActionMenu()
  const { message, show } = useTransientMessage(3000)

  const fetcher = useCallback(() => api.services.list(), [])
  const { data, loading, error, refresh } = useResource(fetcher)
  const services = data?.services ?? []

  const runAction = async (action: () => Promise<unknown>) => {
    // Return focus to the row's trigger (WAI-ARIA menu-button pattern / WCAG
    // 2.4.3), matching the Escape path — activating an item must not strand
    // keyboard focus on <body> once the menuitem unmounts.
    menu.close(true)
    try {
      await action()
      void refresh()
    } catch (e) {
      show(errorMessage(e))
    }
  }

  const handleDelete = async (svc: ServiceItem) => {
    menu.close(true)
    if (!window.confirm(`Delete service "${svc.name}"? This also removes its DNS record and cannot be undone.`)) return
    try {
      // Match the detail page's default: clean up the Cloudflare DNS record so a
      // list-delete doesn't silently orphan a record pointing at a now-dead IP.
      await api.services.remove(svc.id, { cleanupDns: true })
      void refresh()
    } catch (e) {
      show(errorMessage(e))
    }
  }

  if (loading && services.length === 0) {
    return <PageLoading>Loading services...</PageLoading>
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

      {message && (
        <div role="status" className="mt-4 rounded-md bg-yellow-50 px-4 py-2 text-sm text-yellow-800">{message}</div>
      )}

      {error && services.length > 0 && (
        <div role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-800">
          Unable to refresh services: {error}
        </div>
      )}

      {error && services.length === 0 ? (
        <PageError className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
          Unable to load services: {error}
        </PageError>
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
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Service</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Hostname</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Upstream</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Status</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Edge IP</th>
                <th scope="col" className="px-4 py-3 text-left text-xs font-medium uppercase text-zinc-500">Cert Expiry</th>
                <th scope="col" className="px-4 py-3 text-right text-xs font-medium uppercase text-zinc-500">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 bg-white">
              {services.map((svc) => {
                const phase = svc.status?.phase || "pending"
                const cert = formatCertExpiry(svc.status?.cert_expires_at, tz)
                const actions = serviceLifecycleActions(svc.id)
                const menuItems: RowActionMenuItem[] = [
                  { key: "view", label: "View Details", to: `/services/${encodeURIComponent(svc.id)}` },
                  ...(svc.enabled
                    ? [
                        { key: actions.reload.key, label: actions.reload.label, onSelect: () => runAction(actions.reload.run) },
                        { key: actions.restart.key, label: actions.restart.label, onSelect: () => runAction(actions.restart.run) },
                        { key: actions.recreate.key, label: actions.recreate.label, onSelect: () => runAction(actions.recreate.run) },
                      ]
                    : []),
                  svc.enabled
                    ? { key: actions.disable.key, label: actions.disable.label, onSelect: () => runAction(actions.disable.run) }
                    : { key: actions.enable.key, label: actions.enable.label, onSelect: () => runAction(actions.enable.run) },
                  { key: "delete", label: "Delete", onSelect: () => handleDelete(svc), danger: true },
                ]
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
                      <RowActionsMenu rowId={svc.id} items={menuItems} menu={menu} />
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
