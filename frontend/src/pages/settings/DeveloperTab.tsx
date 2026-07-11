import { useCallback, useId, useState } from "react"
import { Loader2, XCircle } from "lucide-react"
import { api, type MainLogsResponse } from "@/lib/api"
import { useResource } from "@/lib/useResource"
import { errorMessage } from "@/lib/utils"

export function DeveloperTab() {
  const [workingAction, setWorkingAction] = useState<"reset-setup-complete" | "reset-all" | null>(null)
  const [actionError, setActionError] = useState("")
  const [logs, setLogs] = useState<MainLogsResponse | null>(null)
  const logsFetcher = useCallback(() => api.settings.mainLogs(), [])
  const { loading: loadingLogs, error: logsError, refresh: refreshLogs, setError: setLogsError } = useResource(logsFetcher, {
    immediate: false,
    mapError: (e) => errorMessage(e, "Failed to load tailBale logs"),
    onData: (data) => {
      setLogs(data)
    },
  })

  const working = workingAction !== null
  const resetSetupDescId = useId()
  const resetAllDescId = useId()

  const runReset = async (kind: "reset-setup-complete" | "reset-all") => {
    const warning =
      kind === "reset-all"
        ? "Reset all will delete the current user, remove all services, clear stored secrets and settings, and send you back to setup. Continue?"
        : "Reset setup_complete will send you back to the setup wizard but keep the existing user, services, and secrets. Continue?"

    if (!window.confirm(warning)) return

    setWorkingAction(kind)
    setActionError("")
    setLogsError(null)
    try {
      await api.settings.developerReset(kind)
      window.location.assign("/setup")
    } catch (e) {
      setActionError(errorMessage(e, "Developer reset failed"))
      setWorkingAction(null)
    }
  }

  const loadLogs = useCallback(async () => {
    setLogs(null)
    setActionError("")
    setLogsError(null)
    await refreshLogs()
  }, [refreshLogs, setLogsError])

  const error = actionError || logsError || ""

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        Dangerous tools. Reset actions are for local testing and recovery only.
      </div>

      <div className="rounded-md border border-zinc-200 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-zinc-800">tailBale container logs</h3>
            <p className="mt-1 text-sm text-zinc-500">
              Shows the latest logs from the main tailBale container.
            </p>
          </div>
          <button
            onClick={loadLogs}
            disabled={loadingLogs}
            aria-busy={loadingLogs}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
          >
            {loadingLogs ? <><Loader2 className="h-4 w-4 animate-spin" /> Loading logs...</> : "Refresh logs"}
          </button>
        </div>
        {loadingLogs && (
          <div className="mt-3 flex items-center gap-2 text-sm text-zinc-500" role="status">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading main container logs...
          </div>
        )}
        {logs && (
          <div className="mt-3">
            <p className="text-xs text-zinc-500">Container: {logs.container}</p>
            <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-zinc-950 p-3 text-xs text-zinc-100" aria-label="Main tailBale container logs">
              {logs.logs || "No logs returned."}
            </pre>
          </div>
        )}
      </div>

      <div className="rounded-md border border-zinc-200 p-4">
        <h3 className="text-sm font-semibold text-zinc-800">Reset setup_complete</h3>
        <p id={resetSetupDescId} className="mt-1 text-sm text-zinc-500">
          Sends the app back to the setup wizard without deleting users, services, or secrets.
        </p>
        <button
          onClick={() => runReset("reset-setup-complete")}
          disabled={working}
          aria-busy={workingAction === "reset-setup-complete"}
          aria-describedby={resetSetupDescId}
          className="mt-3 rounded-md border border-amber-300 px-4 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
        >
          {workingAction === "reset-setup-complete" ? "Working..." : "Reset setup_complete"}
        </button>
      </div>

      <div className="rounded-md border border-red-200 p-4">
        <h3 className="text-sm font-semibold text-red-800">Reset all</h3>
        <p id={resetAllDescId} className="mt-1 text-sm text-zinc-500">
          Attempts to remove every service cleanly, then clears the current user, settings, and stored secrets.
        </p>
        <button
          onClick={() => runReset("reset-all")}
          disabled={working}
          aria-busy={workingAction === "reset-all"}
          aria-describedby={resetAllDescId}
          className="mt-3 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
        >
          {workingAction === "reset-all" ? "Working..." : "Reset all"}
        </button>
      </div>

      {error && (
        <div role="alert" className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
          <XCircle className="h-4 w-4" /> {error}
        </div>
      )}
    </div>
  )
}
