import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import { api, type ServiceItem } from "@/lib/api"
import { useTimezone } from "@/lib/useTimezone"
import { cn, errorMessage } from "@/lib/utils"
import { formatCertExpiry } from "@/lib/certStatus"
import { phaseStyle } from "@/lib/statusStyles"
import { useResource } from "@/lib/useResource"
import { useTransientMessage } from "@/lib/useTransientMessage"
import { Loader2, Plus, ExternalLink, MoreVertical } from "lucide-react"

export default function Services() {
  const navigate = useNavigate()
  const tz = useTimezone()
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null)
  const [menuActiveIndex, setMenuActiveIndex] = useState(0)
  const { message, show } = useTransientMessage(3000)
  // Remember the button that opened the row menu so Escape can restore focus to
  // it (WAI-ARIA menu-button pattern — the trigger declares aria-haspopup).
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null)
  // The open row menu's container, so keyboard navigation can enumerate its
  // menuitems for roving-tabindex focus movement (WAI-ARIA menu pattern).
  const menuRef = useRef<HTMLDivElement | null>(null)

  const fetcher = useCallback(() => api.services.list(), [])
  const { data, loading, error, refresh } = useResource(fetcher)
  const services = data?.services ?? []

  // Close the open row menu on Escape and return focus to its trigger. The
  // backdrop only handles pointer dismissal; keyboard users need a way out that
  // matches the aria-haspopup contract (WCAG 2.1.1).
  useEffect(() => {
    if (openMenuId === null) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      setOpenMenuId(null)
      setMenuPos(null)
      menuTriggerRef.current?.focus()
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [openMenuId])

  // Opening the menu moves focus to its first item and resets the roving
  // tabindex, so Arrow keys drive it immediately (WAI-ARIA menu-button pattern).
  useEffect(() => {
    if (openMenuId === null) return
    setMenuActiveIndex(0)
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
  }, [openMenuId])

  // The menu is position:fixed with coords captured at open time, so scrolling
  // the Layout <main> or resizing would detach it from its trigger. Close it on
  // scroll/resize (FA3); `capture` catches scroll from any ancestor because
  // scroll events don't bubble. A portal would be overkill here.
  useEffect(() => {
    if (openMenuId === null) return
    const close = () => {
      setOpenMenuId(null)
      setMenuPos(null)
    }
    window.addEventListener("scroll", close, true)
    window.addEventListener("resize", close)
    return () => {
      window.removeEventListener("scroll", close, true)
      window.removeEventListener("resize", close)
    }
  }, [openMenuId])

  // Roving-tabindex keyboard navigation for the open row menu: Arrow keys wrap
  // between menuitems, Home/End jump to the ends (WAI-ARIA menu pattern).
  const handleMenuKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    )
    if (items.length === 0) return
    const current = items.findIndex((el) => el === document.activeElement)
    let next: number
    switch (e.key) {
      case "ArrowDown":
        next = current < 0 ? 0 : (current + 1) % items.length
        break
      case "ArrowUp":
        next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
        break
      case "Home":
        next = 0
        break
      case "End":
        next = items.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    setMenuActiveIndex(next)
    items[next].focus()
  }

  const runAction = async (action: () => Promise<unknown>) => {
    setOpenMenuId(null)
    setMenuPos(null)
    try {
      await action()
      void refresh()
    } catch (e) {
      show(errorMessage(e))
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
      show(errorMessage(e))
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

      {message && (
        <div role="status" className="mt-4 rounded-md bg-yellow-50 px-4 py-2 text-sm text-yellow-800">{message}</div>
      )}

      {error && services.length > 0 && (
        <div role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-800">
          Unable to refresh services: {error}
        </div>
      )}

      {error && services.length === 0 ? (
        <div role="alert" className="mt-8 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
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
                const menuItems: Array<
                  | { key: string; label: string; to: string }
                  | { key: string; label: string; onSelect: () => void; danger?: boolean }
                > = [
                  { key: "view", label: "View Details", to: `/services/${encodeURIComponent(svc.id)}` },
                  ...(svc.enabled
                    ? [
                        { key: "reload", label: "Reload Caddy", onSelect: () => runAction(() => api.services.reload(svc.id)) },
                        { key: "restart", label: "Restart Edge", onSelect: () => runAction(() => api.services.restartEdge(svc.id)) },
                        { key: "recreate", label: "Recreate Edge", onSelect: () => runAction(() => api.services.recreateEdge(svc.id)) },
                      ]
                    : []),
                  svc.enabled
                    ? { key: "disable", label: "Disable", onSelect: () => runAction(() => api.services.disable(svc.id)) }
                    : { key: "enable", label: "Enable", onSelect: () => runAction(() => api.services.update(svc.id, { enabled: true })) },
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
                      <div className="inline-block">
                        <button
                          onClick={(e) => {
                            if (openMenuId === svc.id) {
                              setOpenMenuId(null)
                              setMenuPos(null)
                            } else {
                              const btn = e.currentTarget
                              const rect = btn.getBoundingClientRect()
                              setMenuPos({ top: rect.bottom + 4, left: rect.right - 176 })
                              setOpenMenuId(svc.id)
                              menuTriggerRef.current = btn
                            }
                          }}
                          className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
                          aria-label="Actions"
                          aria-haspopup="true"
                          aria-expanded={openMenuId === svc.id}
                        >
                          <MoreVertical className="h-4 w-4" />
                        </button>
                        {openMenuId === svc.id && menuPos && (
                          <>
                            <div className="fixed inset-0 z-10" onClick={() => { setOpenMenuId(null); setMenuPos(null) }} />
                            <div
                              ref={menuRef}
                              role="menu"
                              aria-label="Service actions"
                              className="fixed z-50 w-44 rounded-md border border-zinc-200 bg-white py-1 shadow-lg"
                              style={{ top: menuPos.top, left: menuPos.left }}
                              onKeyDown={handleMenuKeyDown}
                            >
                              {menuItems.map((item, index) => {
                                const tabIndex = index === menuActiveIndex ? 0 : -1
                                const base = "block w-full px-3 py-1.5 text-left text-sm"
                                if ("to" in item) {
                                  return (
                                    <Link
                                      key={item.key}
                                      to={item.to}
                                      role="menuitem"
                                      tabIndex={tabIndex}
                                      className={cn(base, "text-zinc-700 hover:bg-zinc-50")}
                                    >
                                      {item.label}
                                    </Link>
                                  )
                                }
                                return (
                                  <button
                                    key={item.key}
                                    type="button"
                                    role="menuitem"
                                    tabIndex={tabIndex}
                                    onClick={item.onSelect}
                                    className={cn(
                                      base,
                                      item.danger ? "text-red-600 hover:bg-red-50" : "text-zinc-700 hover:bg-zinc-50",
                                    )}
                                  >
                                    {item.label}
                                  </button>
                                )
                              })}
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
