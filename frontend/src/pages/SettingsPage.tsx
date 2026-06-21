import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"
import { setConfiguredTimezone } from "@/lib/useTimezone"
import {
  api,
  type AllSettings,
  type ConnectionTestResult,
  type MainLogsResponse,
} from "@/lib/api"
import {
  CheckCircle,
  XCircle,
  Loader2,
} from "lucide-react"

const ALL_TABS = ["General", "Cloudflare", "Tailscale", "Docker", "Paths", "Account", "Developer"] as const
type Tab = (typeof ALL_TABS)[number]
type SaveHandler = (b: Record<string, unknown>) => Promise<void>
type TestResultState = {
  service: string
  result: ConnectionTestResult
}

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("General")
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<TestResultState | null>(null)
  const [testing, setTesting] = useState(false)
  const [version, setVersion] = useState<string | null>(null)
  const [error, setError] = useState("")

  const load = async () => {
    setLoading(true)
    setError("")
    try {
      const [data, ver] = await Promise.all([
        api.get<AllSettings>("/settings"),
        api.get<{ version: string }>("/version").catch(() => null),
      ])
      setSettings(data)
      if (ver) setVersion(ver.version)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (settings && !settings.general.developer_mode && tab === "Developer") {
      setTab("General")
    }
  }, [settings, tab])

  const save = async (section: string, body: Record<string, unknown>) => {
    setSaving(true)
    setTestResult(null)
    setError("")
    try {
      const data = await api.put<AllSettings>(`/settings/${section}`, body)
      setSettings(data)
      // Keep the shared timezone cache in sync so timestamps across the app
      // reflect a changed timezone without requiring a full page reload.
      if (data.general?.timezone) setConfiguredTimezone(data.general.timezone)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save settings")
      throw e
    } finally {
      setSaving(false)
    }
  }

  const runTest = async (service: string) => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.post<ConnectionTestResult>(`/settings/test/${service}`)
      setTestResult({ service, result })
    } catch (e) {
      setTestResult({ service, result: { success: false, message: e instanceof Error ? e.message : String(e) } })
    } finally {
      setTesting(false)
    }
  }

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
        <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error || "Failed to load settings"}</div>
      </div>
    )
  }

  const tabs = settings.general.developer_mode
    ? ALL_TABS
    : ALL_TABS.filter((item) => item !== "Developer")

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
            aria-selected={t === tab}
            onClick={() => { setTab(t); setTestResult(null) }}
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
        <div className="mt-4 max-w-lg rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}

      <div className="mt-6 max-w-lg" role="tabpanel" aria-label={`${tab} settings`}>
        {tab === "General" && (
          <GeneralTab settings={settings.general} onSave={(b) => save("general", b)} saving={saving} version={version} />
        )}
        {tab === "Cloudflare" && (
          <CloudflareTab
            settings={settings.cloudflare}
            onSave={(b) => save("cloudflare", b)}
            onTest={() => runTest("cloudflare")}
            saving={saving}
            testing={testing}
            testResult={testResult?.service === "cloudflare" ? testResult.result : null}
          />
        )}
        {tab === "Tailscale" && (
          <TailscaleTab
            settings={settings.tailscale}
            onSave={(b) => save("tailscale", b)}
            onTest={() => runTest("tailscale")}
            saving={saving}
            testing={testing}
            testResult={testResult?.service === "tailscale" ? testResult.result : null}
          />
        )}
        {tab === "Docker" && (
          <DockerTab
            settings={settings.docker}
            onSave={(b) => save("docker", b)}
            onTest={() => runTest("docker")}
            saving={saving}
            testing={testing}
            testResult={testResult?.service === "docker" ? testResult.result : null}
          />
        )}
        {tab === "Paths" && (
          <PathsTab settings={settings.paths} onSave={(b) => save("paths", b)} saving={saving} />
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

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  hint?: string
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-zinc-700">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
      />
      {hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>}
    </label>
  )
}

function SaveButton({ saving, onClick, label }: { saving: boolean; onClick: () => void | Promise<void>; label?: string }) {
  const handleClick = () => {
    void Promise.resolve(onClick()).catch(() => undefined)
  }
  return (
    <button
      onClick={handleClick}
      disabled={saving}
      className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
    >
      {saving ? "Saving..." : label ?? "Save"}
    </button>
  )
}

function TestButton({ testing, onClick, label }: { testing: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={testing}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
    >
      {testing ? <><Loader2 className="h-4 w-4 animate-spin" /> Testing...</> : label}
    </button>
  )
}

function TestResultBanner({ result }: { result: ConnectionTestResult }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
        result.success ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
      )}
    >
      {result.success ? <CheckCircle className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
      {result.message}
    </div>
  )
}

function SecretStatus({ configured }: { configured: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        configured ? "bg-green-100 text-green-700" : "bg-zinc-100 text-zinc-500"
      )}
    >
      {configured ? (
        <><CheckCircle className="h-3 w-3" /> Configured</>
      ) : (
        "Not set"
      )}
    </span>
  )
}

function GeneralTab({
  settings,
  onSave,
  saving,
  version,
}: {
  settings: AllSettings["general"]
  onSave: SaveHandler
  saving: boolean
  version: string | null
}) {
  const [baseDomain, setBaseDomain] = useState(settings.base_domain)
  const [acmeEmail, setAcmeEmail] = useState(settings.acme_email)
  const [reconcileInterval, setReconcileInterval] = useState(String(settings.reconcile_interval_seconds))
  const [renewalWindow, setRenewalWindow] = useState(String(settings.cert_renewal_window_days))
  const [timezone, setTimezone] = useState(settings.timezone)
  const [developerMode, setDeveloperMode] = useState(settings.developer_mode)

  useEffect(() => {
    setBaseDomain(settings.base_domain)
    setAcmeEmail(settings.acme_email)
    setReconcileInterval(String(settings.reconcile_interval_seconds))
    setRenewalWindow(String(settings.cert_renewal_window_days))
    setTimezone(settings.timezone)
    setDeveloperMode(settings.developer_mode)
  }, [settings])

  return (
    <div className="space-y-4">
      <Field label="Base Domain" value={baseDomain} onChange={setBaseDomain} placeholder="mydomain.com" />
      <Field label="ACME Email" value={acmeEmail} onChange={setAcmeEmail} type="email" placeholder="admin@mydomain.com" />
      <Field
        label="Reconcile Interval (seconds)"
        value={reconcileInterval}
        onChange={setReconcileInterval}
        type="number"
        hint="How often the reconciler sweeps all services"
      />
      <Field
        label="Cert Renewal Window (days)"
        value={renewalWindow}
        onChange={setRenewalWindow}
        type="number"
        hint="Renew certs this many days before expiry"
      />
      <label className="block">
        <span className="text-sm font-medium text-zinc-700">Timezone</span>
        <select
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
        >
          {Intl.supportedValuesOf("timeZone").map((tzName: string) => (
            <option key={tzName} value={tzName}>{tzName.replace(/_/g, " ")}</option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-400">Timezone used for all displayed times</p>
      </label>
      <label className="flex items-start gap-3 rounded-md border border-zinc-200 px-3 py-3">
        <input
          type="checkbox"
          checked={developerMode}
          onChange={(e) => setDeveloperMode(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-zinc-300 text-zinc-900 focus:ring-zinc-500"
        />
        <span>
          <span className="block text-sm font-medium text-zinc-700">Developer Mode</span>
          <span className="mt-1 block text-xs text-zinc-400">
            Shows advanced reset controls, including setup reset and full data reset.
          </span>
        </span>
      </label>
      <SaveButton
        saving={saving}
        onClick={() =>
          onSave({
            base_domain: baseDomain,
            acme_email: acmeEmail,
            reconcile_interval_seconds: Number(reconcileInterval),
            cert_renewal_window_days: Number(renewalWindow),
            timezone,
            developer_mode: developerMode,
          })
        }
      />

      <div className="mt-6 border-t border-zinc-200 pt-4">
        <div className="text-sm text-zinc-500">
          <span className="font-medium text-zinc-700">tailBale</span>
          {version ? (
            <span className="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600">
              v{version}
            </span>
          ) : (
            <span className="ml-2 text-xs text-zinc-400">version unknown</span>
          )}
        </div>
      </div>
    </div>
  )
}

function CloudflareTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["cloudflare"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [zoneId, setZoneId] = useState(settings.zone_id)
  const [token, setToken] = useState("")

  useEffect(() => {
    setZoneId(settings.zone_id)
  }, [settings.zone_id])

  const handleSave = async () => {
    await onSave({ zone_id: zoneId, token: token || undefined })
    setToken("")
  }

  return (
    <div className="space-y-4">
      <Field label="Zone ID" value={zoneId} onChange={setZoneId} placeholder="Cloudflare zone ID" />
      <div>
        <Field
          label="API Token"
          value={token}
          onChange={setToken}
          type="password"
          placeholder="Enter new token to update"
          hint="Write-only — current value is never shown"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.token_configured} />
        </div>
      </div>
      <div className="flex gap-2">
        <SaveButton
          saving={saving}
          onClick={handleSave}
        />
        <TestButton testing={testing} onClick={onTest} label="Test Connection" />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </div>
  )
}

function TailscaleTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["tailscale"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [authKey, setAuthKey] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [controlUrl, setControlUrl] = useState(settings.control_url)
  const [prefix, setPrefix] = useState(settings.default_ts_hostname_prefix)

  useEffect(() => {
    setControlUrl(settings.control_url)
    setPrefix(settings.default_ts_hostname_prefix)
  }, [settings.control_url, settings.default_ts_hostname_prefix])

  const handleSave = async () => {
    await onSave({
      auth_key: authKey || undefined,
      api_key: apiKey || undefined,
      control_url: controlUrl,
      default_ts_hostname_prefix: prefix,
    })
    setAuthKey("")
    setApiKey("")
  }

  return (
    <div className="space-y-4">
      <div>
        <Field
          label="Auth Key"
          value={authKey}
          onChange={setAuthKey}
          type="password"
          placeholder="tskey-auth-..."
          hint="Write-only — used to register edge containers on your tailnet"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.auth_key_configured} />
        </div>
      </div>
      <div>
        <Field
          label="API Key"
          value={apiKey}
          onChange={setApiKey}
          type="password"
          placeholder="tskey-api-..."
          hint="Write-only — used to remove devices from tailnet on service deletion"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.api_key_configured} />
        </div>
      </div>
      <Field label="Control URL" value={controlUrl} onChange={setControlUrl} placeholder="https://controlplane.tailscale.com" />
      <Field
        label="Default TS Hostname Prefix"
        value={prefix}
        onChange={setPrefix}
        placeholder="edge"
        hint="Edge containers will be named <prefix>-<service-slug>"
      />
      <div className="flex gap-2">
        <SaveButton
          saving={saving}
          onClick={handleSave}
        />
        <TestButton testing={testing} onClick={onTest} label="Validate Key" />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </div>
  )
}

function DockerTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["docker"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [socketPath, setSocketPath] = useState(settings.socket_path)

  useEffect(() => {
    setSocketPath(settings.socket_path)
  }, [settings.socket_path])

  return (
    <div className="space-y-4">
      <Field
        label="Docker Socket Path"
        value={socketPath}
        onChange={setSocketPath}
        placeholder="unix:///var/run/docker.sock"
      />
      <div className="flex gap-2">
        <SaveButton saving={saving} onClick={() => onSave({ socket_path: socketPath })} />
        <TestButton testing={testing} onClick={onTest} label="Test Connection" />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </div>
  )
}

function PathsTab({
  settings,
  onSave,
  saving,
}: {
  settings: AllSettings["paths"]
  onSave: SaveHandler
  saving: boolean
}) {
  const [generated, setGenerated] = useState(settings.generated_root)
  const [cert, setCert] = useState(settings.cert_root)
  const [ts, setTs] = useState(settings.tailscale_state_root)

  useEffect(() => {
    setGenerated(settings.generated_root)
    setCert(settings.cert_root)
    setTs(settings.tailscale_state_root)
  }, [settings.generated_root, settings.cert_root, settings.tailscale_state_root])

  return (
    <div className="space-y-4">
      <Field
        label="Generated Config Root"
        value={generated}
        onChange={setGenerated}
        placeholder="Leave blank to use default (data/generated)"
        hint="Where generated Caddyfiles are stored"
      />
      <Field
        label="Certificate Root"
        value={cert}
        onChange={setCert}
        placeholder="Leave blank to use default (data/certs)"
        hint="Where TLS certificates are stored"
      />
      <Field
        label="Tailscale State Root"
        value={ts}
        onChange={setTs}
        placeholder="Leave blank to use default (data/tailscale)"
        hint="Where Tailscale state directories are stored"
      />
      <SaveButton
        saving={saving}
        onClick={() =>
          onSave({ generated_root: generated, cert_root: cert, tailscale_state_root: ts })
        }
      />
    </div>
  )
}

function AccountTab() {
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState("")
  const [error, setError] = useState("")

  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= 8 &&
    newPassword === confirmPassword &&
    !saving

  const handleChangePassword = async () => {
    setSaving(true)
    setError("")
    setSuccess("")
    try {
      await api.post("/auth/change-password", {
        current_password: currentPassword,
        new_password: newPassword,
      })
      setSuccess("Password changed successfully")
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to change password")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-zinc-700">Change Password</h3>
        <div className="mt-3 space-y-3">
          <Field
            label="Current Password"
            value={currentPassword}
            onChange={(v) => { setCurrentPassword(v); setError(""); setSuccess("") }}
            type="password"
            placeholder="Enter current password"
          />
          <Field
            label="New Password"
            value={newPassword}
            onChange={(v) => { setNewPassword(v); setError(""); setSuccess("") }}
            type="password"
            placeholder="Minimum 8 characters"
            hint="Minimum 8 characters"
          />
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Confirm New Password</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => { setConfirmPassword(e.target.value); setError(""); setSuccess("") }}
              placeholder="Confirm new password"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
            {confirmPassword.length > 0 && newPassword !== confirmPassword && (
              <p className="mt-1 text-xs text-red-600">Passwords do not match.</p>
            )}
          </label>
          <button
            onClick={handleChangePassword}
            disabled={!canSubmit}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
          >
            {saving ? "Changing..." : "Change Password"}
          </button>
          {success && (
            <div className="flex items-center gap-2 rounded-md bg-green-50 px-3 py-2 text-sm text-green-800">
              <CheckCircle className="h-4 w-4" /> {success}
            </div>
          )}
          {error && (
            <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
              <XCircle className="h-4 w-4" /> {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DeveloperTab() {
  const [workingAction, setWorkingAction] = useState<"reset-setup-complete" | "reset-all" | null>(null)
  const [loadingLogs, setLoadingLogs] = useState(false)
  const [error, setError] = useState("")
  const [logs, setLogs] = useState<MainLogsResponse | null>(null)

  const working = workingAction !== null

  const runReset = async (kind: "reset-setup-complete" | "reset-all") => {
    const warning =
      kind === "reset-all"
        ? "Reset all will delete the current user, remove all services, clear stored secrets and settings, and send you back to setup. Continue?"
        : "Reset setup_complete will send you back to the setup wizard but keep the existing user, services, and secrets. Continue?"

    if (!window.confirm(warning)) return

    setWorkingAction(kind)
    setError("")
    try {
      await api.post(`/settings/developer/${kind}`)
      window.location.assign("/setup")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Developer reset failed")
      setWorkingAction(null)
    }
  }

  const loadLogs = async () => {
    setLoadingLogs(true)
    setError("")
    setLogs(null)
    try {
      setLogs(await api.get<MainLogsResponse>("/settings/developer/main-logs?tail=250"))
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load tailBale logs")
    } finally {
      setLoadingLogs(false)
    }
  }

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
        <p className="mt-1 text-sm text-zinc-500">
          Sends the app back to the setup wizard without deleting users, services, or secrets.
        </p>
        <button
          onClick={() => runReset("reset-setup-complete")}
          disabled={working}
          className="mt-3 rounded-md border border-amber-300 px-4 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
        >
          {workingAction === "reset-setup-complete" ? "Working..." : "Reset setup_complete"}
        </button>
      </div>

      <div className="rounded-md border border-red-200 p-4">
        <h3 className="text-sm font-semibold text-red-800">Reset all</h3>
        <p className="mt-1 text-sm text-zinc-500">
          Attempts to remove every service cleanly, then clears the current user, settings, and stored secrets.
        </p>
        <button
          onClick={() => runReset("reset-all")}
          disabled={working}
          className="mt-3 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
        >
          {workingAction === "reset-all" ? "Working..." : "Reset all"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
          <XCircle className="h-4 w-4" /> {error}
        </div>
      )}
    </div>
  )
}
