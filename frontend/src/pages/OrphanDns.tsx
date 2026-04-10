import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { useTimezone, formatDateTime } from "@/lib/useTimezone"
import { Loader2, AlertTriangle, RefreshCw, Trash2, CheckCircle2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface JobDetails {
  record_id: string
  hostname: string
  zone_id: string
  value: string | null
  service_name: string
}

interface OrphanJob {
  id: string
  service_id: string | null
  kind: string
  status: string
  progress: number
  message: string | null
  details: JobDetails | null
  created_at: string | null
  updated_at: string | null
}

interface JobsResponse {
  jobs: OrphanJob[]
  total: number
}

export default function OrphanDns() {
  const tz = useTimezone()
  const [jobs, setJobs] = useState<OrphanJob[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({})
  const [successMessage, setSuccessMessage] = useState("")

  async function load() {
    setLoading(true)
    setError("")
    try {
      const data = await api.get<JobsResponse>("/jobs?kind=dns_orphan_cleanup")
      setJobs(data.jobs)
      setTotal(data.total)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load orphan records")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function handleRetry(job: OrphanJob) {
    setActionLoading((prev) => ({ ...prev, [job.id]: "retry" }))
    setSuccessMessage("")
    try {
      const result = await api.post<{ success: boolean; message: string }>(
        `/jobs/${job.id}/retry`
      )
      setSuccessMessage(result.message)
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Retry failed")
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
    try {
      await api.delete(`/jobs/${job.id}`)
      setSuccessMessage(`Orphan record for '${hostname}' dismissed`)
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Dismiss failed")
    } finally {
      setActionLoading((prev) => {
        const next = { ...prev }
        delete next[job.id]
        return next
      })
    }
  }

  function fmtTime(iso: string | null) {
    if (!iso) return "\u2014"
    return formatDateTime(iso, tz)
  }

  const STATUS_STYLES: Record<string, string> = {
    pending: "bg-yellow-100 text-yellow-800",
    running: "bg-blue-100 text-blue-700",
    failed: "bg-red-100 text-red-700",
    completed: "bg-green-100 text-green-700",
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Orphaned DNS Records</h1>
      <p className="mt-1 text-zinc-500">
        DNS records left in Cloudflare after a service was deleted. Retry to
        remove them, or dismiss if you've cleaned them up manually.
      </p>

      {successMessage && (
        <div className="mt-4 flex items-center gap-2 rounded-md bg-green-50 px-4 py-3 text-green-700">
          <CheckCircle2 className="h-4 w-4" />
          {successMessage}
        </div>
      )}

      {error && (
        <div className="mt-4 flex items-center gap-2 rounded-md bg-red-50 px-4 py-3 text-red-700">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      )}

      {loading ? (
        <div className="mt-8 flex items-center gap-2 text-zinc-500">
          <Loader2 className="h-5 w-5 animate-spin" /> Loading...
        </div>
      ) : jobs.length === 0 ? (
        <div className="mt-8 rounded-lg border border-dashed border-zinc-300 p-8 text-center text-zinc-500">
          No orphaned DNS records. All clean!
        </div>
      ) : (
        <>
          <div className="mt-4 text-sm text-zinc-500">{total} orphaned record{total !== 1 ? "s" : ""}</div>
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
                            STATUS_STYLES[job.status] ?? "bg-zinc-100 text-zinc-600"
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
                        <div>Created: {fmtTime(job.created_at)}</div>
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
        </>
      )}
    </div>
  )
}
