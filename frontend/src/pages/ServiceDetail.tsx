import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { api, type EdgeVersionResponse } from "@/lib/api"
import { useResource } from "@/lib/useResource"
import { useTimezone, formatDate } from "@/lib/useTimezone"
import { cn, errorMessage } from "@/lib/utils"
import { phaseStyle, phaseLabel } from "@/lib/statusStyles"
import { useTransientMessage } from "@/lib/useTransientMessage"
import { PageError, PageLoading } from "@/components/PageState"
import { ArrowLeft } from "lucide-react"
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

  const { message, show, clear } = useTransientMessage(4000)
  const [edgeVersion, setEdgeVersion] = useState<EdgeVersionResponse | null>(null)
  const [updatingEdge, setUpdatingEdge] = useState(false)
  const edgeVersionFetcher = useCallback(() => api.services.edgeVersion(id ?? ""), [id])
  const { refresh: refreshEdgeVersion } = useResource(edgeVersionFetcher, {
    immediate: false,
    onData: (v) => {
      setEdgeVersion(v)
    },
  })

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
      show(errorMessage(e))
    } finally {
      setUpdatingEdge(false)
    }
  }, [id, loadEdgeVersion, refresh, show])

  // Clear the transient action message when navigating between services (confirm
  // dialogs + the edit form reset via ServiceActions' key remount / the hook).
  useEffect(() => {
    clear()
  }, [id, clear])

  useEffect(() => {
    // Deferred a microtask so the async function's synchronous prefix
    // (setEdgeVersion(null) inside loadEdgeVersion) runs outside the
    // effect's own callback frame, not synchronously within it.
    void Promise.resolve().then(() => loadEdgeVersion())
  }, [loadEdgeVersion])

  if (loading) {
    return <PageLoading>Loading...</PageLoading>
  }

  if (error && !service) {
    return (
      <div>
        <button onClick={() => navigate("/services")} className="mb-4 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700">
          <ArrowLeft className="h-4 w-4" /> Back to Services
        </button>
        <PageError>{error}</PageError>
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
            {phaseLabel(phase)}
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
      {message && (
        <div role="status" className="mt-4 rounded-md bg-yellow-50 px-4 py-3 text-sm text-yellow-800">{message}</div>
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
        showActionMsg={show}
        clearActionMsg={clear}
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

const LOG_TABS = [
  { key: "edge", label: "Edge Logs" },
  { key: "events", label: "Events" },
] as const
type LogTab = (typeof LOG_TABS)[number]["key"]

function LogsTabs() {
  const [logsTab, setLogsTab] = useState<LogTab>("edge")
  // Roving tabindex + Arrow/Home/End keyboard navigation for the WAI-ARIA
  // tablist (APG tabs pattern), matching SettingsPage's tablist. Only the
  // active tab is in the tab sequence; arrow keys move (and follow) selection.
  const tabRefs = useRef<Partial<Record<LogTab, HTMLButtonElement | null>>>({})

  const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = LOG_TABS.findIndex((t) => t.key === logsTab)
    let nextIndex: number
    switch (e.key) {
      case "ArrowRight":
        nextIndex = (currentIndex + 1) % LOG_TABS.length
        break
      case "ArrowLeft":
        nextIndex = (currentIndex - 1 + LOG_TABS.length) % LOG_TABS.length
        break
      case "Home":
        nextIndex = 0
        break
      case "End":
        nextIndex = LOG_TABS.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    const nextKey = LOG_TABS[nextIndex].key
    setLogsTab(nextKey)
    tabRefs.current[nextKey]?.focus()
  }

  return (
    <div className="mt-6 rounded-md border border-zinc-200">
      <div className="flex border-b border-zinc-200" role="tablist" aria-label="Service logs">
        {LOG_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`logs-tab-${t.key}`}
            aria-selected={logsTab === t.key}
            aria-controls="logs-panel"
            tabIndex={logsTab === t.key ? 0 : -1}
            ref={(el) => { tabRefs.current[t.key] = el }}
            onKeyDown={onTabKeyDown}
            onClick={() => setLogsTab(t.key)}
            className={cn(
              "px-4 py-2.5 text-sm font-medium",
              logsTab === t.key ? "border-b-2 border-zinc-900 text-zinc-900" : "text-zinc-500 hover:text-zinc-700"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        className="flex min-h-[200px] items-center justify-center p-6"
        role="tabpanel"
        id="logs-panel"
        aria-labelledby={`logs-tab-${logsTab}`}
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
