import { useEffect, useState } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { api, type ServiceItem, type ServiceUpdateRequest } from "@/lib/api"
import { cn } from "@/lib/utils"
import {
  Loader2,
  ArrowLeft,
  Trash2,
  Power,
  PowerOff,
  Save,
  RefreshCw,
  RotateCcw,
  PackagePlus,
  ShieldCheck,
  Play,
  CheckCircle2,
  XCircle,
} from "lucide-react"

const PHASE_STYLES: Record<string, string> = {
  healthy: "bg-green-100 text-green-700",
  pending: "bg-yellow-100 text-yellow-700",
  warning: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
  failed: "bg-red-100 text-red-700",
}

const CHECK_LABELS: Record<string, string> = {
  upstream_container_present: "Upstream Container",
  upstream_network_connected: "Network Connected",
  edge_container_present: "Edge Container",
  edge_container_running: "Edge Running",
  tailscale_ready: "Tailscale Ready",
  tailscale_ip_present: "Tailscale IP",
  cert_present: "Certificate",
  cert_not_expiring: "Cert Valid",
  dns_record_present: "DNS Record",
  dns_matches_ip: "DNS Matches IP",
  caddy_config_present: "Caddy Config",
}

const CHECK_SUGGESTIONS: Record<string, string> = {
  upstream_container_present: "The upstream Docker container is not found. Ensure it is running.",
  upstream_network_connected: "The upstream container is not connected to the edge network. Try re-running reconcile.",
  edge_container_present: "Edge container does not exist. Use 'Recreate Edge' to create it.",
  edge_container_running: "Edge container exists but is not running. Try 'Restart Edge'.",
  tailscale_ready: "Tailscale is not ready inside the edge container. Check the edge logs or recreate the edge.",
  tailscale_ip_present: "No Tailscale IP assigned yet. Wait a moment or recreate the edge container.",
  cert_present: "TLS certificate files are missing. Use 'Force Renew Cert' to issue a new certificate.",
  cert_not_expiring: "Certificate is expiring soon (within 14 days). Use 'Force Renew Cert' to renew.",
  dns_record_present: "No DNS record found. Re-run reconcile to create the Cloudflare DNS record.",
  dns_matches_ip: "DNS record IP does not match the current Tailscale IP. Re-run reconcile to update.",
  caddy_config_present: "Caddyfile is missing. Re-run reconcile to generate the configuration.",
}

export default function ServiceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [service, setService] = useState<ServiceItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [cleanupDns, setCleanupDns] = useState(true)
  const [confirmDisable, setConfirmDisable] = useState(false)
  const [confirmRecreate, setConfirmRecreate] = useState(false)
  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [logsTab, setLogsTab] = useState<"edge" | "events">("edge")

  // Edit form state
  const [editName, setEditName] = useState("")
  const [editPort, setEditPort] = useState("")
  const [editScheme, setEditScheme] = useState("http")
  const [editHealthcheck, setEditHealthcheck] = useState("")
  const [editPreserveHost, setEditPreserveHost] = useState(true)
  const [editSnippet, setEditSnippet] = useState("")

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const svc = await api.get<ServiceItem>(`/services/${id}`)
      setService(svc)
      setEditName(svc.name)
      setEditPort(String(svc.upstream_port))
      setEditScheme(svc.upstream_scheme)
      setEditHealthcheck(svc.healthcheck_path || "")
      setEditPreserveHost(svc.preserve_host_header)
      setEditSnippet(svc.custom_caddy_snippet || "")
    } catch {
      setError("Service not found")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [id])

  const handleSave = async () => {
    setSaving(true)
    try {
      const body: ServiceUpdateRequest = {
        name: editName,
        upstream_port: Number(editPort),
        upstream_scheme: editScheme,
        healthcheck_path: editHealthcheck || null,
        preserve_host_header: editPreserveHost,
        custom_caddy_snippet: editSnippet || null,
      }
      const svc = await api.put<ServiceItem>(`/services/${id}`, body)
      setService(svc)
      setEditing(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const handleToggleEnabled = async () => {
    if (!service) return
    setConfirmDisable(false)
    try {
      if (service.enabled) {
        const svc = await api.post<ServiceItem>(`/services/${id}/disable`)
        setService(svc)
      } else {
        const svc = await api.put<ServiceItem>(`/services/${id}`, { enabled: true })
        setService(svc)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const handleRecreateEdge = async () => {
    setConfirmRecreate(false)
    handleAction("/recreate-edge")
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      const qs = cleanupDns ? "?cleanup_dns=true" : ""
      await api.delete(`/services/${id}${qs}`)
      navigate("/services")
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setDeleting(false)
    }
  }

  const handleAction = async (path: string) => {
    setActionMsg(null)
    try {
      await api.post(`/services/${id}${path}`)
      load()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setActionMsg(msg)
      setTimeout(() => setActionMsg(null), 4000)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading...
      </div>
    )
  }

  if (error && !service) {
    return (
      <div>
        <button onClick={() => navigate("/services")} className="mb-4 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700">
          <ArrowLeft className="h-4 w-4" /> Back to Services
        </button>
        <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      </div>
    )
  }

  if (!service) return null
  const phase = service.status?.phase || "pending"
  const healthChecks = service.status?.health_checks

  return (
    <div>
      <button onClick={() => navigate("/services")} className="mb-4 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700">
        <ArrowLeft className="h-4 w-4" /> Back to Services
      </button>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{service.name}</h1>
          <p className="mt-1 text-sm text-zinc-500">{service.hostname}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn(
            "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
            PHASE_STYLES[phase] || "bg-zinc-100 text-zinc-600"
          )}>
            {phase}
          </span>
          <span className={cn(
            "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
            service.enabled ? "bg-green-100 text-green-700" : "bg-zinc-100 text-zinc-500"
          )}>
            {service.enabled ? "Enabled" : "Disabled"}
          </span>
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}
      {actionMsg && (
        <div className="mt-4 rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">{actionMsg}</div>
      )}

      {/* Info / Edit / Health */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Configuration section */}
        <div className="rounded-md border border-zinc-200 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-700">Configuration</h2>
            {!editing && (
              <button onClick={() => setEditing(true)} className="text-xs font-medium text-zinc-500 hover:text-zinc-700">
                Edit
              </button>
            )}
          </div>

          {editing ? (
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="text-xs font-medium text-zinc-600">Name</span>
                <input type="text" value={editName} onChange={(e) => setEditName(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-zinc-600">Upstream Port</span>
                <input type="number" value={editPort} onChange={(e) => setEditPort(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
              </label>
              <label className="block">
                <span className="text-xs font-medium text-zinc-600">Scheme</span>
                <select value={editScheme} onChange={(e) => setEditScheme(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500">
                  <option value="http">HTTP</option>
                  <option value="https">HTTPS</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-zinc-600">Healthcheck Path</span>
                <input type="text" value={editHealthcheck} onChange={(e) => setEditHealthcheck(e.target.value)}
                  placeholder="/health"
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={editPreserveHost} onChange={(e) => setEditPreserveHost(e.target.checked)} className="rounded border-zinc-300" />
                <span className="text-zinc-600">Preserve Host Header</span>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-zinc-600">Custom Caddy Snippet</span>
                <textarea value={editSnippet} onChange={(e) => setEditSnippet(e.target.value)} rows={2}
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
              </label>
              <div className="flex gap-2">
                <button onClick={handleSave} disabled={saving}
                  className="inline-flex items-center gap-1 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50">
                  {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  Save
                </button>
                <button onClick={() => {
                  if (service) {
                    setEditName(service.name)
                    setEditPort(String(service.upstream_port))
                    setEditScheme(service.upstream_scheme)
                    setEditHealthcheck(service.healthcheck_path || "")
                    setEditPreserveHost(service.preserve_host_header)
                    setEditSnippet(service.custom_caddy_snippet || "")
                  }
                  setEditing(false)
                }} className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <dl className="mt-3 space-y-2 text-sm">
              <Row label="Upstream" value={`${service.upstream_scheme}://${service.upstream_container_name}:${service.upstream_port}`} />
              <Row label="Hostname" value={service.hostname} />
              <Row label="Base Domain" value={service.base_domain} />
              <Row label="Healthcheck" value={service.healthcheck_path || "—"} />
              <Row label="Preserve Host" value={service.preserve_host_header ? "Yes" : "No"} />
              <Row label="App Profile" value={service.app_profile || "—"} />
            </dl>
          )}
        </div>

        {/* Runtime section */}
        <div className="rounded-md border border-zinc-200 p-4">
          <h2 className="text-sm font-semibold text-zinc-700">Runtime</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <Row label="Edge Container" value={service.edge_container_name} />
            <Row label="Docker Network" value={service.network_name} />
            <Row label="TS Hostname" value={service.ts_hostname} />
            <Row label="Tailscale IP" value={service.status?.tailscale_ip || "—"} />
            <Row label="Cert Expiry" value={service.status?.cert_expires_at ? new Date(service.status.cert_expires_at).toLocaleDateString() : "—"} />
            <Row label="Phase" value={phase} />
            <Row label="Message" value={service.status?.message || "—"} />
            <Row label="Last Reconciled" value={service.status?.last_reconciled_at || "Never"} />
          </dl>
        </div>
      </div>

      {/* Health Checks */}
      <div className="mt-6 rounded-md border border-zinc-200 p-4">
        <h2 className="text-sm font-semibold text-zinc-700">Health Checks</h2>
        {healthChecks && Object.keys(healthChecks).length > 0 ? (
          <>
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {Object.entries(healthChecks).map(([key, ok]) => (
                <div key={key} className="flex items-center gap-2 text-sm" title={!ok ? CHECK_SUGGESTIONS[key] : undefined}>
                  {ok ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500" />
                  ) : (
                    <XCircle className="h-4 w-4 text-red-500" />
                  )}
                  <span className="text-zinc-700">{CHECK_LABELS[key] || key}</span>
                </div>
              ))}
            </div>
            {/* Show actionable suggestions for failing checks */}
            {Object.entries(healthChecks).some(([, ok]) => !ok) && (
              <div className="mt-3 space-y-1 rounded-md bg-red-50 p-3">
                {Object.entries(healthChecks)
                  .filter(([, ok]) => !ok)
                  .map(([key]) => (
                    <p key={key} className="text-xs text-red-700">
                      <strong>{CHECK_LABELS[key] || key}:</strong> {CHECK_SUGGESTIONS[key] || "Check failed."}
                    </p>
                  ))}
              </div>
            )}
          </>
        ) : (
          <p className="mt-3 text-sm text-zinc-400">No health checks available yet.</p>
        )}
      </div>

      {/* Actions */}
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
          <button onClick={() => handleAction("/reload")}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
            <RefreshCw className="h-4 w-4" /> Reload Caddy
          </button>
          <button onClick={() => handleAction("/restart-edge")}
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

          <button onClick={() => handleAction("/renew-cert")}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
            <ShieldCheck className="h-4 w-4" /> Force Renew Cert
          </button>
          <button onClick={() => handleAction("/reconcile")}
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

      {/* Logs Tabs */}
      <div className="mt-6 rounded-md border border-zinc-200">
        <div className="flex border-b border-zinc-200">
          <button
            onClick={() => setLogsTab("edge")}
            className={cn(
              "px-4 py-2.5 text-sm font-medium",
              logsTab === "edge" ? "border-b-2 border-zinc-900 text-zinc-900" : "text-zinc-500 hover:text-zinc-700"
            )}
          >
            Edge Logs
          </button>
          <button
            onClick={() => setLogsTab("events")}
            className={cn(
              "px-4 py-2.5 text-sm font-medium",
              logsTab === "events" ? "border-b-2 border-zinc-900 text-zinc-900" : "text-zinc-500 hover:text-zinc-700"
            )}
          >
            Events
          </button>
        </div>
        <div className="flex min-h-[200px] items-center justify-center p-6">
          <p className="text-sm text-zinc-400">
            {logsTab === "edge"
              ? "Edge container logs will appear here once the reconciler is running."
              : "Service events will appear here."}
          </p>
        </div>
      </div>

      <div className="mt-4 text-right">
        <span className="text-xs text-zinc-400">
          Created {new Date(service.created_at).toLocaleDateString()}
        </span>
      </div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="font-medium text-zinc-700">{value}</dd>
    </div>
  )
}
