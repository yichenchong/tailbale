import { useCallback, useEffect, useRef, useState } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { api, type EdgeVersionResponse } from "@/lib/api"
import { useResource } from "@/lib/useResource"
import { useTimezone, formatDate } from "@/lib/useTimezone"
import { cn, errorMessage } from "@/lib/utils"
import { phaseStyle } from "@/lib/statusStyles"
import { Loader2, ArrowLeft } from "lucide-react"
import { useServiceDetail } from "@/components/service/useServiceDetail"
import { ServiceEditForm } from "@/components/service/ServiceEditForm"
import { EdgeVersionPanel } from "@/components/service/EdgeVersionPanel"
import { HealthChecksPanel } from "@/components/service/HealthChecksPanel"
import { ServiceActions } from "@/components/service/ServiceActions"

export default function ServiceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const tz = useTimezone()

  const {
    service,
    loading,
    error,
    refresh,
    applyServiceUpdate,
    setError,
    edit,
  } = useServiceDetail(id)

  const [actionMsg, setActionMsg] = useState<string | null>(null)
  const [edgeVersion, setEdgeVersion] = useState<EdgeVersionResponse | null>(null)
  const [updatingEdge, setUpdatingEdge] = useState(false)
  const actionMsgTimerRef = useRef<number | null>(null)
  const edgeVersionFetcher = useCallback(() => api.services.edgeVersion(id ?? ""), [id])
  const { refresh: refreshEdgeVersion } = useResource(edgeVersionFetcher, {
    immediate: false,
    onData: (v) => {
      setEdgeVersion(v)
    },
  })

  const clearActionMsgTimer = useCallback(() => {
    if (actionMsgTimerRef.current !== null) {
      clearTimeout(actionMsgTimerRef.current)
      actionMsgTimerRef.current = null
    }
  }, [])

  const showActionMsg = useCallback((msg: string) => {
    clearActionMsgTimer()
    setActionMsg(msg)
    actionMsgTimerRef.current = window.setTimeout(() => {
      setActionMsg(null)
      actionMsgTimerRef.current = null
    }, 4000)
  }, [clearActionMsgTimer])

  // Soft-clear the visible message without touching the pending clear timer, so a
  // newer message's timer stays the one authority over when it disappears.
  const clearActionMsg = useCallback(() => setActionMsg(null), [])

  const loadEdgeVersion = useCallback(async () => {
    setEdgeVersion(null)
    await refreshEdgeVersion()
  }, [refreshEdgeVersion])

  const handleUpdateEdge = useCallback(async () => {
    setUpdatingEdge(true)
    try {
      await api.services.updateEdge(id ?? "")
      await loadEdgeVersion()
      void refresh({ background: true })
    } catch (e) {
      showActionMsg(errorMessage(e))
    } finally {
      setUpdatingEdge(false)
    }
  }, [id, loadEdgeVersion, refresh, showActionMsg])

  // Clear the transient action message when navigating between services (confirm
  // dialogs + the edit form reset via ServiceActions' key remount / the hook).
  useEffect(() => {
    clearActionMsgTimer()
    setActionMsg(null)
  }, [id, clearActionMsgTimer])

  useEffect(() => {
    void loadEdgeVersion()
  }, [loadEdgeVersion])

  useEffect(() => () => clearActionMsgTimer(), [clearActionMsgTimer])

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
        <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      </div>
    )
  }

  if (!service) return null
  const phase = service.status?.phase || "pending"

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
            phaseStyle(phase)
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
        <div role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}
      {actionMsg && (
        <div role="status" className="mt-4 rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">{actionMsg}</div>
      )}

      {/* Info / Edit / Runtime */}
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <ServiceEditForm
          service={service}
          id={id}
          edit={edit}
          applyServiceUpdate={applyServiceUpdate}
          setError={setError}
        />
        <EdgeVersionPanel service={service} edgeVersion={edgeVersion} tz={tz} />
      </div>

      <HealthChecksPanel status={service.status} tz={tz} />

      <ServiceActions
        key={id}
        service={service}
        id={id}
        edgeVersion={edgeVersion}
        updatingEdge={updatingEdge}
        onUpdateEdge={handleUpdateEdge}
        refresh={refresh}
        showActionMsg={showActionMsg}
        clearActionMsg={clearActionMsg}
        applyServiceUpdate={applyServiceUpdate}
        setError={setError}
      />

      {/* Logs Tabs */}
      <LogsTabs />

      <div className="mt-4 text-right">
        <span className="text-xs text-zinc-400">
          Created {formatDate(service.created_at, tz)}
        </span>
      </div>
    </div>
  )
}

function LogsTabs() {
  const [logsTab, setLogsTab] = useState<"edge" | "events">("edge")
  return (
    <div className="mt-6 rounded-md border border-zinc-200">
      <div className="flex border-b border-zinc-200" role="tablist" aria-label="Service logs">
        <button
          type="button"
          role="tab"
          id="logs-tab-edge"
          aria-selected={logsTab === "edge"}
          aria-controls="logs-panel"
          onClick={() => setLogsTab("edge")}
          className={cn(
            "px-4 py-2.5 text-sm font-medium",
            logsTab === "edge" ? "border-b-2 border-zinc-900 text-zinc-900" : "text-zinc-500 hover:text-zinc-700"
          )}
        >
          Edge Logs
        </button>
        <button
          type="button"
          role="tab"
          id="logs-tab-events"
          aria-selected={logsTab === "events"}
          aria-controls="logs-panel"
          onClick={() => setLogsTab("events")}
          className={cn(
            "px-4 py-2.5 text-sm font-medium",
            logsTab === "events" ? "border-b-2 border-zinc-900 text-zinc-900" : "text-zinc-500 hover:text-zinc-700"
          )}
        >
          Events
        </button>
      </div>
      <div
        className="flex min-h-[200px] items-center justify-center p-6"
        role="tabpanel"
        id="logs-panel"
        aria-labelledby={logsTab === "edge" ? "logs-tab-edge" : "logs-tab-events"}
      >
        <p className="text-sm text-zinc-400">
          {logsTab === "edge"
            ? "Edge container logs will appear here once the reconciler is running."
            : "Service events will appear here."}
        </p>
      </div>
    </div>
  )
}
