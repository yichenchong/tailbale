import { useCallback, useState } from "react"
import { api, type OrphanJob, type JobsResponse } from "@/lib/api"
import { useTimezone, formatDateTimeOrDash } from "@/lib/useTimezone"
import { Loader2, AlertTriangle, RefreshCw, Trash2, CheckCircle2 } from "lucide-react"
import { cn, errorMessage } from "@/lib/utils"
import { jobStatusStyle } from "@/lib/statusStyles"
import { usePaginatedResource } from "@/lib/usePaginatedResource"
import { PaginationBar } from "@/components/PaginationBar"
import { PageLoading } from "@/components/PageState"
import { ResourceBoundary } from "@/components/ResourceBoundary"

export default function OrphanDns() {
  const tz = useTimezone()
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({})
  const [successMessage, setSuccessMessage] = useState("")
  const loadJobs = useCallback(
    ({ limit, offset }: { limit: number; offset: number }) =>
      api.jobs.list({ kind: "dns_orphan_cleanup", limit, offset }),
    [],
  )
  const getJobs = useCallback((response: JobsResponse) => response.jobs, [])
  const resource = usePaginatedResource<JobsResponse, OrphanJob>({
    load: loadJobs,
    getItems: getJobs,
    mapError: (e) => (errorMessage(e, "Failed to load orphan records")),
  })
  const { items, loading, error, refresh, setError, total } = resource
  const jobs = items

  async function handleRetry(job: OrphanJob) {
    setActionLoading((prev) => ({ ...prev, [job.id]: "retry" }))
    setSuccessMessage("")
    setError(null)
    try {
      const result = await api.jobs.retry(job.id)
      setSuccessMessage(result.message)
      await refresh({ background: true })
    } catch (e: unknown) {
      setError(errorMessage(e, "Retry failed"))
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev }
        delete next[job.id]
        return next
      })
    }
  }

  async function handleDismiss(job: OrphanJob) {
    const hostname = job.details?.hostname ?? "this record"
    if (!confirm(`Dismiss orphan record for '${hostname}'?\n\nThis will NOT delete the Cloudflare record — use this only if you've already cleaned it up manually.`)) {
      return
    }
    setActionLoading((prev) => ({ ...prev, [job.id]: "dismiss" }))
    setSuccessMessage("")
    setError(null)
    try {
      await api.jobs.dismiss(job.id)
      setSuccessMessage(`Orphan record for '${hostname}' dismissed`)
      await refresh({ background: true })
    } catch (e: unknown) {
      setError(errorMessage(e, "Dismiss failed"))
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev }
        delete next[job.id]
        return next
      })
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Orphaned DNS Records</h1>
      <p className="mt-1 text-zinc-500">
        DNS records left in Cloudflare after a service was deleted. Retry to
        remove them, or dismiss if you've cleaned them up manually.
      </p>

      {successMessage && (
        <div role="status" className="mt-4 flex items-center gap-2 rounded-md bg-green-50 px-4 py-3 text-green-700">
          <CheckCircle2 className="h-4 w-4" />
          {successMessage}
        </div>
      )}

      {error && (
        <div role="alert" className="mt-4 flex items-center gap-2 rounded-md bg-red-50 px-4 py-3 text-red-700">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}

      <ResourceBoundary
        loading={loading}
        empty={jobs.length === 0}
        loadingSlot={
          <PageLoading className="mt-8 flex items-center gap-2 text-zinc-500" iconClassName="h-5 w-5 animate-spin">
            Loading...
          </PageLoading>
        }
        emptySlot={
          // Don't claim "All clean!" when the list is empty only because the load
          // failed — the error banner above already explains what happened.
          error ? null : (
            <div className="mt-8 rounded-lg border border-dashed border-zinc-300 p-8 text-center text-zinc-500">
              No orphaned DNS records. All clean!
            </div>
          )
        }
      >
        <div className="mt-4 text-sm text-zinc-500">
          {total} orphaned record{total !== 1 ? "s" : ""}
        </div>
        <div className="mt-2 space-y-3">
          {jobs.map((job) => {
            const d = job.details
            const busy = actionLoading[job.id]
            return (
              <div
                key={job.id}
                className="rounded-lg border bg-white p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-zinc-900">
                        {d?.hostname ?? "Unknown hostname"}
                      </span>
                      <span
                        className={cn(
                          "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                          jobStatusStyle(job.status)
                        )}
                      >
                        {job.status}
                      </span>
                    </div>
                    <div className="mt-1 space-y-0.5 text-sm text-zinc-500">
                      {d?.service_name && (
                        <div>Service: <span className="text-zinc-700">{d.service_name}</span></div>
                      )}
                      {d?.record_id && (
                        <div>Record ID: <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">{d.record_id}</code></div>
                      )}
                      {d?.value && (
                        <div>IP: <code className="rounded bg-zinc-100 px-1 py-0.5 text-xs">{d.value}</code></div>
                      )}
                      <div>Created: {formatDateTimeOrDash(job.created_at, tz)}</div>
                      {job.message && (
                        <div className="mt-1 text-xs text-zinc-400">{job.message}</div>
                      )}
                    </div>
                  </div>

                  <div className="flex shrink-0 gap-2">
                    <button
                      onClick={() => handleRetry(job)}
                      disabled={!!busy}
                      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 shadow-sm hover:bg-zinc-50 disabled:opacity-50"
                    >
                      {busy === "retry" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <RefreshCw className="h-3.5 w-3.5" />
                      )}
                      Retry Deletion
                    </button>
                    <button
                      onClick={() => handleDismiss(job)}
                      disabled={!!busy}
                      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-red-600 shadow-sm hover:bg-red-50 disabled:opacity-50"
                    >
                      {busy === "dismiss" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                      Dismiss
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        <PaginationBar resource={resource} />
      </ResourceBoundary>
    </div>
  )
}
