import { useEffect, useRef } from "react"
import {
  Loader2,
  Trash2,
  Power,
  PowerOff,
  RefreshCw,
  RotateCcw,
  PackagePlus,
  ShieldCheck,
  Play,
  AlertTriangle,
} from "lucide-react"
import { api, type ServiceItem, type EdgeVersionResponse } from "@/lib/api"
import { useServiceActions } from "./useServiceActions"

/**
 * Lifecycle action bar + confirmation flows for a service: enable/disable (with a
 * confirm before disabling), reload/restart/recreate edge, update-edge, certificate
 * renewal (with the force-renew modal), re-run reconcile, and delete (with the DNS
 * -cleanup confirm). Success/soft-failure feedback goes through the page-level
 * `showActionMsg`; delete/toggle hard errors go through `setError`. Mutation,
 * confirmation, and loading state live in `useServiceActions`; this component is
 * remounted (resetting that state) on navigation via a `key={id}`.
 */
export function ServiceActions({
  service,
  id,
  edgeVersion,
  updatingEdge,
  onUpdateEdge,
  refresh,
  showActionMsg,
  clearActionMsg,
  applyServiceUpdate,
  setError,
}: {
  service: ServiceItem
  id: string | undefined
  edgeVersion: EdgeVersionResponse | null
  updatingEdge: boolean
  onUpdateEdge: () => void
  refresh: (opts?: { background?: boolean }) => Promise<void>
  showActionMsg: (msg: string) => void
  clearActionMsg: () => void
  applyServiceUpdate: (svc: ServiceItem) => void
  setError: (value: string | null) => void
}) {
  const {
    deleting,
    confirmDelete,
    setConfirmDelete,
    cleanupDns,
    setCleanupDns,
    confirmDisable,
    setConfirmDisable,
    confirmRecreate,
    setConfirmRecreate,
    confirmForceRenew,
    setConfirmForceRenew,
    renewing,
    runAction,
    handleToggleEnabled,
    handleRecreateEdge,
    handleDelete,
    handleRenewCert,
    handleForceRenewCert,
  } = useServiceActions({
    service,
    id,
    refresh,
    showActionMsg,
    clearActionMsg,
    applyServiceUpdate,
    setError,
  })

  const dialogRef = useRef<HTMLDivElement | null>(null)
  const forceRenewTriggerRef = useRef<HTMLElement | null>(null)

  // Focus management for the force-renew dialog (WCAG 2.4.3): on open, remember
  // the element that triggered it and move focus into the dialog; trap Tab
  // within it and close on Escape; on close, restore focus to the trigger.
  useEffect(() => {
    if (!confirmForceRenew) return
    forceRenewTriggerRef.current = document.activeElement as HTMLElement | null
    const focusables = () =>
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    focusables()?.[0]?.focus()
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault()
        setConfirmForceRenew(false)
        return
      }
      if (e.key !== "Tab") return
      const items = focusables()
      if (!items || items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("keydown", onKeyDown)
      forceRenewTriggerRef.current?.focus()
    }
  }, [confirmForceRenew, setConfirmForceRenew])

  return (
    <>
      <div className="mt-6 border-t border-zinc-200 pt-4">
        <h2 className="mb-3 text-sm font-semibold text-zinc-700">Actions</h2>
        <div className="flex flex-wrap items-center gap-2">
          {/* Disable/Enable with confirmation */}
          {confirmDisable ? (
            <div className="flex items-center gap-2 rounded-md border border-yellow-200 bg-yellow-50 px-3 py-1.5">
              <span className="text-sm text-yellow-800">Disable this service? The edge container will stop receiving traffic.</span>
              <button onClick={handleToggleEnabled}
                className="rounded bg-yellow-600 px-2 py-1 text-xs font-medium text-white hover:bg-yellow-700">
                Disable
              </button>
              <button onClick={() => setConfirmDisable(false)}
                className="text-xs text-yellow-700 hover:underline">Cancel</button>
            </div>
          ) : (
            <button onClick={() => service.enabled ? setConfirmDisable(true) : handleToggleEnabled()}
              className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
              {service.enabled ? <><PowerOff className="h-4 w-4" /> Disable</> : <><Power className="h-4 w-4" /> Enable</>}
            </button>
          )}
          {service.enabled && (
            <>
              <button onClick={() => runAction(() => api.services.reload(id ?? ""))}
                className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
                <RefreshCw className="h-4 w-4" /> Reload Caddy
              </button>
              <button onClick={() => runAction(() => api.services.restartEdge(id ?? ""))}
                className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
                <RotateCcw className="h-4 w-4" /> Restart Edge
              </button>

              {/* Recreate Edge with confirmation */}
              {confirmRecreate ? (
                <div className="flex items-center gap-2 rounded-md border border-yellow-200 bg-yellow-50 px-3 py-1.5">
                  <span className="text-sm text-yellow-800">Recreate edge? This will cause brief downtime.</span>
                  <button onClick={handleRecreateEdge}
                    className="rounded bg-yellow-600 px-2 py-1 text-xs font-medium text-white hover:bg-yellow-700">
                    Recreate
                  </button>
                  <button onClick={() => setConfirmRecreate(false)}
                    className="text-xs text-yellow-700 hover:underline">Cancel</button>
                </div>
              ) : (
                <button onClick={() => setConfirmRecreate(true)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
                  <PackagePlus className="h-4 w-4" /> Recreate Edge
                </button>
              )}

              {edgeVersion && !edgeVersion.up_to_date && (
                <button onClick={onUpdateEdge} disabled={updatingEdge}
                  className="inline-flex items-center gap-1.5 rounded-md border border-yellow-300 bg-yellow-50 px-3 py-1.5 text-sm font-medium text-yellow-700 hover:bg-yellow-100 disabled:opacity-50">
                  {updatingEdge ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  Update Edge
                </button>
              )}
            </>
          )}
          <button onClick={handleRenewCert} disabled={renewing}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50">
            {renewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />} Renew certificate
          </button>
          <button onClick={() => runAction(() => api.services.reconcile(id ?? ""))}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
            <Play className="h-4 w-4" /> Re-run Reconcile
          </button>

          {/* Delete with cleanup checkboxes */}
          {confirmDelete ? (
            <div className="ml-auto space-y-2 rounded-md border border-red-200 bg-red-50 p-3">
              <p className="text-sm font-medium text-red-800">Delete "{service.name}"?</p>
              <label className="flex items-center gap-2 text-sm text-red-700">
                <input type="checkbox" checked={cleanupDns} onChange={(e) => setCleanupDns(e.target.checked)}
                  className="rounded border-red-300" />
                Remove DNS record from Cloudflare
              </label>
              <div className="flex gap-2">
                <button onClick={handleDelete} disabled={deleting}
                  className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50">
                  {deleting ? "Deleting..." : "Delete Service"}
                </button>
                <button onClick={() => setConfirmDelete(false)}
                  className="rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-100">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button onClick={() => setConfirmDelete(true)}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50">
              <Trash2 className="h-4 w-4" /> Delete
            </button>
          )}
        </div>
      </div>

      {/* Force-renew confirmation modal. Only shown after a non-force renew is
          refused (needs_force): the cert is healthy and far from expiry. */}
      {confirmForceRenew && (
        <div
          ref={dialogRef}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="force-renew-title"
        >
          <div className="w-full max-w-md rounded-md bg-white p-5 shadow-lg">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 flex-shrink-0 text-yellow-500" />
              <div>
                <h3 id="force-renew-title" className="text-base font-semibold text-zinc-900">
                  Force certificate renewal?
                </h3>
                <p className="mt-2 text-sm text-zinc-600">
                  This certificate is healthy and not near expiry, so it does not need to be
                  renewed yet.
                </p>
                <p className="mt-2 text-sm text-zinc-600">
                  Forcing a renewal now contacts Let's Encrypt and counts against its rate
                  limits (e.g. 5 duplicate certificates per registered domain per week). Only
                  force a renewal if you have a specific reason to.
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setConfirmForceRenew(false)}
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-600 hover:bg-zinc-50"
              >
                Cancel
              </button>
              <button
                onClick={handleForceRenewCert}
                disabled={renewing}
                className="inline-flex items-center gap-1.5 rounded-md bg-yellow-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-yellow-700 disabled:opacity-50"
              >
                {renewing ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                Force renew
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
