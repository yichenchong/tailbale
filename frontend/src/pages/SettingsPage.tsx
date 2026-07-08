import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { cn } from "@/lib/utils"
import { Loader2 } from "lucide-react"
import { useSettings } from "./settings/useSettings"
import { GeneralTab } from "./settings/GeneralTab"
import { CloudflareTab } from "./settings/CloudflareTab"
import { TailscaleTab } from "./settings/TailscaleTab"
import { DockerTab } from "./settings/DockerTab"
import { PathsTab } from "./settings/PathsTab"
import { AccountTab } from "./settings/AccountTab"
import { DeveloperTab } from "./settings/DeveloperTab"

const ALL_TABS = ["General", "Cloudflare", "Tailscale", "Docker", "Paths", "Account", "Developer"] as const
type Tab = (typeof ALL_TABS)[number]

const TABPANEL_ID = "settings-tabpanel"
const tabButtonId = (t: Tab) => `settings-tab-${t}`

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("General")
  const tabRefs = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const {
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
  } = useSettings()

  useEffect(() => {
    if (settings && !settings.general.developer_mode && tab === "Developer") {
      setTab("General")
    }
  }, [settings, tab])

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading settings...
      </div>
    )
  }

  if (!settings) {
    return (
      <div className="p-8">
        <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error || "Failed to load settings"}</div>
      </div>
    )
  }

  const tabs: readonly Tab[] = settings.general.developer_mode
    ? ALL_TABS
    : ALL_TABS.filter((item) => item !== "Developer")

  const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = tabs.indexOf(tab)
    let nextIndex: number
    switch (e.key) {
      case "ArrowRight":
        nextIndex = (currentIndex + 1) % tabs.length
        break
      case "ArrowLeft":
        nextIndex = (currentIndex - 1 + tabs.length) % tabs.length
        break
      case "Home":
        nextIndex = 0
        break
      case "End":
        nextIndex = tabs.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    const nextTab = tabs[nextIndex]
    setTab(nextTab)
    setTestResult(null)
    setError("")
    tabRefs.current[nextTab]?.focus()
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Settings</h1>
      <p className="mt-1 text-sm text-zinc-500">Configure tailBale orchestrator.</p>

      <div className="mt-6 flex gap-1 border-b border-zinc-200" role="tablist" aria-label="Settings sections">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={tabButtonId(t)}
            aria-selected={t === tab}
            aria-controls={TABPANEL_ID}
            tabIndex={t === tab ? 0 : -1}
            ref={(el) => { tabRefs.current[t] = el }}
            onKeyDown={onTabKeyDown}
            onClick={() => { setTab(t); setTestResult(null); setError("") }}
            className={cn(
              "px-4 py-2 text-sm font-medium transition-colors",
              t === tab
                ? "border-b-2 border-zinc-900 text-zinc-900"
                : "text-zinc-500 hover:text-zinc-700"
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div role="alert" className="mt-4 max-w-lg rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}

      <div className="mt-6 max-w-lg" role="tabpanel" id={TABPANEL_ID} aria-labelledby={tabButtonId(tab)}>
        {tab === "General" && (
          <GeneralTab settings={settings.general} onSave={(b) => save("general", b)} saving={savingSection === "general"} version={version} />
        )}
        {tab === "Cloudflare" && (
          <CloudflareTab
            settings={settings.cloudflare}
            onSave={(b) => save("cloudflare", b)}
            onTest={() => runTest("cloudflare")}
            saving={savingSection === "cloudflare"}
            testing={testingService === "cloudflare"}
            testResult={testResult?.service === "cloudflare" ? testResult.result : null}
          />
        )}
        {tab === "Tailscale" && (
          <TailscaleTab
            settings={settings.tailscale}
            onSave={(b) => save("tailscale", b)}
            onTest={() => runTest("tailscale")}
            saving={savingSection === "tailscale"}
            testing={testingService === "tailscale"}
            testResult={testResult?.service === "tailscale" ? testResult.result : null}
          />
        )}
        {tab === "Docker" && (
          <DockerTab
            settings={settings.docker}
            onSave={(b) => save("docker", b)}
            onTest={() => runTest("docker")}
            saving={savingSection === "docker"}
            testing={testingService === "docker"}
            testResult={testResult?.service === "docker" ? testResult.result : null}
          />
        )}
        {tab === "Paths" && (
          <PathsTab settings={settings.paths} onSave={(b) => save("paths", b)} saving={savingSection === "paths"} />
        )}
        {tab === "Account" && (
          <AccountTab />
        )}
        {tab === "Developer" && (
          <DeveloperTab />
        )}
      </div>
    </div>
  )
}
