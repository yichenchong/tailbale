import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"
import {
  api,
  type AllSettings,
  type ConnectionTestResult,
} from "@/lib/api"
import {
  CheckCircle,
  XCircle,
  Loader2,
} from "lucide-react"

const TABS = ["General", "Cloudflare", "Tailscale", "Docker", "Paths", "Account"] as const
type Tab = (typeof TABS)[number]

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("General")
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [version, setVersion] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [data, ver] = await Promise.all([
        api.get<AllSettings>("/settings"),
        api.get<{ version: string }>("/version").catch(() => null),
      ])
      setSettings(data)
      if (ver) setVersion(ver.version)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const save = async (section: string, body: Record<string, unknown>) => {
    setSaving(true)
    setTestResult(null)
    try {
      const data = await api.put<AllSettings>(`/settings/${section}`, body)
      setSettings(data)
    } finally {
      setSaving(false)
    }
  }

  const runTest = async (service: string) => {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.post<ConnectionTestResult>(`/settings/test/${service}`)
      setTestResult(result)
    } catch (e) {
      setTestResult({ success: false, message: String(e) })
    } finally {
      setTesting(false)
    }
  }

  if (loading || !settings) {
    return (
      <div className="flex items-center gap-2 p-8 text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading settings...
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-2xl font-bold">Settings</h1>
      <p className="mt-1 text-sm text-zinc-500">Configure tailBale orchestrator.</p>

      {/* Tab bar */}
      <div className="mt-6 flex gap-1 border-b border-zinc-200">
        {TABS.map((t) => (
          <button
            key={t}
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

      {/* Tab content */}
      <div className="mt-6 max-w-lg">
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
            testResult={testResult}
          />
        )}
        {tab === "Tailscale" && (
          <TailscaleTab
            settings={settings.tailscale}
            onSave={(b) => save("tailscale", b)}
            onTest={() => runTest("tailscale")}
            saving={saving}
            testing={testing}
            testResult={testResult}
          />
        )}
        {tab === "Docker" && (
          <DockerTab
            settings={settings.docker}
            onSave={(b) => save("docker", b)}
            onTest={() => runTest("docker")}
            saving={saving}
            testing={testing}
            testResult={testResult}
          />
        )}
        {tab === "Paths" && (
          <PathsTab settings={settings.paths} onSave={(b) => save("paths", b)} saving={saving} />
        )}
        {tab === "Account" && (
          <AccountTab />
        )}
      </div>
    </div>
  )
}

// --- Shared components ---

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

function SaveButton({ saving, onClick, label }: { saving: boolean; onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
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
      onClick={onClick}
      disabled={testing}
      className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
    >
      {testing ? <Loader2 className="inline h-4 w-4 animate-spin" /> : label}
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

// --- Tab components ---

function GeneralTab({
  settings,
  onSave,
  saving,
  version,
}: {
  settings: AllSettings["general"]
  onSave: (b: Record<string, unknown>) => void
  saving: boolean
  version: string | null
}) {
  const [baseDomain, setBaseDomain] = useState(settings.base_domain)
  const [acmeEmail, setAcmeEmail] = useState(settings.acme_email)
  const [reconcileInterval, setReconcileInterval] = useState(String(settings.reconcile_interval_seconds))
  const [renewalWindow, setRenewalWindow] = useState(String(settings.cert_renewal_window_days))
  const [timezone, setTimezone] = useState(settings.timezone)

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
          {Intl.supportedValuesOf("timeZone").map((tz) => (
            <option key={tz} value={tz}>{tz.replace(/_/g, " ")}</option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-400">Timezone used for all displayed times</p>
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
          })
        }
      />

      {/* Version info */}
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
  onSave: (b: Record<string, unknown>) => void
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [zoneId, setZoneId] = useState(settings.zone_id)
  const [token, setToken] = useState("")

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
          onClick={() => onSave({ zone_id: zoneId, token: token || undefined })}
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
  onSave: (b: Record<string, unknown>) => void
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [authKey, setAuthKey] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [controlUrl, setControlUrl] = useState(settings.control_url)
  const [prefix, setPrefix] = useState(settings.default_ts_hostname_prefix)

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
          onClick={() =>
            onSave({
              auth_key: authKey || undefined,
              api_key: apiKey || undefined,
              control_url: controlUrl,
              default_ts_hostname_prefix: prefix,
            })
          }
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
  onSave: (b: Record<string, unknown>) => void
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const [socketPath, setSocketPath] = useState(settings.socket_path)

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
  onSave: (b: Record<string, unknown>) => void
  saving: boolean
}) {
  const [generated, setGenerated] = useState(settings.generated_root)
  const [cert, setCert] = useState(settings.cert_root)
  const [ts, setTs] = useState(settings.tailscale_state_root)

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
      {/* Password change */}
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
