import { useCallback, useEffect, useRef, useState } from "react"
import { setConfiguredTimezone } from "@/lib/useTimezone"
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
 * Settings-page data machine: load (with a stale-response guard), save (with a
 * last-writer-wins guard + timezone-cache sync), and connection-test (with its
 * own sequence guard). Extracted verbatim from the former SettingsPage
 * controller so the page becomes a thin tab router.
 */
export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingSection, setSavingSection] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<TestResultState | null>(null)
  const [testingService, setTestingService] = useState<string | null>(null)
  const [version, setVersion] = useState<string | null>(null)
  const [error, setError] = useState("")
  const loadSeqRef = useRef(0)
  const testSeqRef = useRef(0)

  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current
    setLoading(true)
    setError("")
    try {
      const [data, ver] = await Promise.all([
        api.settings.all(),
        api.meta.version().catch(() => null),
      ])
      if (seq !== loadSeqRef.current) return
      setSettings(data)
      if (ver) setVersion(ver.version)
    } catch (e) {
      if (seq !== loadSeqRef.current) return
      setError(e instanceof Error ? e.message : "Failed to load settings")
    } finally {
      if (seq === loadSeqRef.current) setLoading(false)
    }
  }, [])

  const applySettingsUpdate = useCallback((data: AllSettings) => {
    // A save is the authoritative latest write. Bump the load sequence so any
    // load still in flight (mount/poll/refresh) is discarded instead of
    // clobbering this fresh state. Keep the shared timezone cache in sync so
    // timestamps across the app reflect a changed timezone without a reload.
    loadSeqRef.current += 1
    setSettings(data)
    if (data.general?.timezone) setConfiguredTimezone(data.general.timezone)
  }, [])

  useEffect(() => {
    void load()
    return () => {
      loadSeqRef.current += 1
    }
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
        setError(e instanceof Error ? e.message : "Failed to save settings")
        throw e
      } finally {
        setSavingSection(null)
      }
    },
    [applySettingsUpdate],
  )

  const runTest = useCallback(async (service: SettingsTestService) => {
    const seq = ++testSeqRef.current
    setTestingService(service)
    setTestResult(null)
    setError("")
    try {
      const result = await api.settings.test(service)
      if (seq !== testSeqRef.current) return
      setTestResult({ service, result })
    } catch (e) {
      if (seq !== testSeqRef.current) return
      setTestResult({ service, result: { success: false, message: e instanceof Error ? e.message : String(e) } })
    } finally {
      if (seq === testSeqRef.current) setTestingService(null)
    }
  }, [])

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
