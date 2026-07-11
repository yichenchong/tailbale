import { useCallback, useEffect, useState } from "react"
import { useLatestRequest } from "@/lib/useLatestRequest"
import { setConfiguredTimezone } from "@/lib/useTimezone"
import { errorMessage } from "@/lib/utils"
import {
  api,
  type AllSettings,
  type ConnectionTestResult,
  type SettingsSection,
  type SettingsTestService,
} from "@/lib/api"

/** A tab's save callback: PUT a section body and adopt the server response. */
export type SaveHandler = (b: Record<string, unknown>) => Promise<void>

type TestResultState = {
  service: string
  result: ConnectionTestResult
}

export interface UseSettingsResult {
  settings: AllSettings | null
  loading: boolean
  error: string
  version: string | null
  savingSection: string | null
  testResult: TestResultState | null
  testingService: string | null
  save: (section: SettingsSection, body: Record<string, unknown>) => Promise<void>
  runTest: (service: SettingsTestService) => Promise<void>
  setError: (value: string) => void
  setTestResult: (value: TestResultState | null) => void
}

/**
 * Settings-page data machine: load (with a shared stale-response guard), save
 * (with a last-writer-wins invalidation + timezone-cache sync), and
 * connection-test (with its own shared request guard). Extracted from the
 * former SettingsPage controller so the page becomes a thin tab router.
 */
export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingSection, setSavingSection] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<TestResultState | null>(null)
  const [testingService, setTestingService] = useState<string | null>(null)
  const [version, setVersion] = useState<string | null>(null)
  const [error, setError] = useState("")
  const loadRequest = useLatestRequest()
  const testRequest = useLatestRequest()

  const load = useCallback(async () => {
    const token = loadRequest.next()
    setLoading(true)
    setError("")
    try {
      const [data, ver] = await Promise.all([
        api.settings.all(),
        api.meta.version().catch(() => null),
      ])
      if (!loadRequest.isCurrent(token)) return
      setSettings(data)
      if (ver) setVersion(ver.version)
    } catch (e) {
      if (!loadRequest.isCurrent(token)) return
      setError(errorMessage(e, "Failed to load settings"))
    } finally {
      if (loadRequest.isCurrent(token)) setLoading(false)
    }
  }, [loadRequest])

  const applySettingsUpdate = useCallback((data: AllSettings) => {
    // A save is the authoritative latest write. Invalidate any load still in
    // flight (mount/poll/refresh) so it is discarded instead of clobbering this
    // fresh state. Keep the shared timezone cache in sync so timestamps across
    // the app reflect a changed timezone without a reload.
    loadRequest.invalidate()
    setSettings(data)
    if (data.general?.timezone) setConfiguredTimezone(data.general.timezone)
  }, [loadRequest])

  useEffect(() => {
    // Deferred a microtask so `load`'s synchronous prefix (setLoading(true) /
    // setError("")) runs outside the effect's own callback frame.
    void Promise.resolve().then(() => load())
  }, [load])

  const save = useCallback(
    async (section: SettingsSection, body: Record<string, unknown>) => {
      setSavingSection(section)
      setTestResult(null)
      setError("")
      try {
        const data = await api.settings.update(section, body)
        applySettingsUpdate(data)
      } catch (e) {
        setError(errorMessage(e, "Failed to save settings"))
        throw e
      } finally {
        setSavingSection(null)
      }
    },
    [applySettingsUpdate],
  )

  const runTest = useCallback(async (service: SettingsTestService) => {
    const token = testRequest.next()
    setTestingService(service)
    setTestResult(null)
    setError("")
    try {
      const result = await api.settings.test(service)
      if (!testRequest.isCurrent(token)) return
      setTestResult({ service, result })
    } catch (e) {
      if (!testRequest.isCurrent(token)) return
      setTestResult({ service, result: { success: false, message: errorMessage(e) } })
    } finally {
      if (testRequest.isCurrent(token)) setTestingService(null)
    }
  }, [testRequest])

  return {
    settings,
    loading,
    error,
    version,
    savingSection,
    testResult,
    testingService,
    save,
    runTest,
    setError,
    setTestResult,
  }
}
