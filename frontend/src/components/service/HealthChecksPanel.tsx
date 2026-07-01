import { CheckCircle2, XCircle, Clock } from "lucide-react"
import { type ServiceStatus } from "@/lib/api"
import { formatDateTime } from "@/lib/useTimezone"
import { ProbeRetryBanner } from "./ProbeRetryBanner"

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
  https_probe_ok: "HTTPS Probe",
}

const CHECK_SUGGESTIONS: Record<string, string> = {
  upstream_container_present: "The upstream Docker container is not found. Ensure it is running.",
  upstream_network_connected: "The upstream container is not connected to the edge network. Try re-running reconcile.",
  edge_container_present: "Edge container does not exist. Use 'Recreate Edge' to create it.",
  edge_container_running: "Edge container exists but is not running. Try 'Restart Edge'.",
  tailscale_ready: "Tailscale is not ready inside the edge container. Check the edge logs or recreate the edge.",
  tailscale_ip_present: "No Tailscale IP assigned yet. Wait a moment or recreate the edge container.",
  cert_present: "TLS certificate files are missing. Use 'Renew certificate' to issue a new certificate.",
  cert_not_expiring: "Certificate is expiring soon. Use 'Renew certificate' to renew.",
  dns_record_present: "No DNS record found. Re-run reconcile to create the Cloudflare DNS record.",
  dns_matches_ip: "DNS record IP does not match the current Tailscale IP. Re-run reconcile to update.",
  caddy_config_present: "Caddyfile is missing. Re-run reconcile to generate the configuration.",
  https_probe_ok: "HTTPS probe failed — Caddy may still be starting or the upstream is unreachable.",
}

/**
 * Health Checks card: the per-check pass/fail grid, last-probe timestamp, the
 * live probe-retry countdown, and the actionable suggestions box for any failing
 * checks. Renders a placeholder until the reconciler has produced check results.
 */
export function HealthChecksPanel({
  status,
  tz,
}: {
  status: ServiceStatus | null | undefined
  tz: string
}) {
  const healthChecks = status?.health_checks
  return (
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

          {/* Last probe timestamp */}
          {status?.last_probe_at && (
            <div className="mt-3 flex items-center gap-1.5 text-xs text-zinc-500">
              <Clock className="h-3.5 w-3.5" />
              Last checked {formatDateTime(status.last_probe_at, tz)}
            </div>
          )}

          {/* Probe retry info when HTTPS probe is failing */}
          {healthChecks.https_probe_ok === false && status?.probe_retry_at && (
            <ProbeRetryBanner
              retryAt={status.probe_retry_at}
              attempt={status.probe_retry_attempt}
              tz={tz}
            />
          )}

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
  )
}
