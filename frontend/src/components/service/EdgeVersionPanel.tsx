import { AlertTriangle } from "lucide-react"
import { type ServiceItem, type EdgeVersionResponse } from "@/lib/api"
import { formatDateTime } from "@/lib/useTimezone"
import { formatCertExpiry } from "@/lib/certStatus"
import { Row } from "./Row"

/**
 * Runtime card: edge/network/tailscale identifiers, the certificate expiry (color
 * -coded by urgency via {@link formatCertExpiry}), reconcile phase/message, and —
 * once the edge-version probe resolves — the running edge image version plus an
 * "outdated" banner when it lags the orchestrator.
 */
export function EdgeVersionPanel({
  service,
  edgeVersion,
  tz,
}: {
  service: ServiceItem
  edgeVersion: EdgeVersionResponse | null
  tz: string
}) {
  const phase = service.status?.phase || "pending"
  const cert = formatCertExpiry(service.status?.cert_expires_at, tz)
  return (
    <div className="rounded-md border border-zinc-200 p-4">
      <h2 className="text-sm font-semibold text-zinc-700">Runtime</h2>
      <dl className="mt-3 space-y-2 text-sm">
        <Row label="Edge Container" value={service.edge_container_name} />
        <Row label="Docker Network" value={service.network_name} />
        <Row label="TS Hostname" value={service.ts_hostname} />
        <Row label="Tailscale IP" value={service.status?.tailscale_ip || "—"} />
        <Row label="Cert Expiry" value={cert.text} valueClassName={cert.style} />
        <Row label="Phase" value={phase} />
        <Row label="Message" value={service.status?.message || "—"} />
        <Row label="Last Reconciled" value={service.status?.last_reconciled_at ? formatDateTime(service.status.last_reconciled_at, tz) : "Never"} />
        {edgeVersion && (
          <>
            <Row label="Edge Version" value={edgeVersion.edge_version || "unknown"} />
            <Row label="App Version" value={edgeVersion.orchestrator_version} />
          </>
        )}
      </dl>
      {edgeVersion && !edgeVersion.up_to_date && (
        <div className="mt-3 flex items-center gap-2 rounded-md bg-yellow-50 px-3 py-2 text-xs text-yellow-700">
          <AlertTriangle className="h-3.5 w-3.5" />
          Edge container is outdated ({edgeVersion.edge_version || "unknown"} vs {edgeVersion.orchestrator_version})
        </div>
      )}
    </div>
  )
}

