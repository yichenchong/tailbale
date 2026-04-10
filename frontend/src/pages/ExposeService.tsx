import { useState, useEffect } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { api, type ServiceItem, type AllSettings, type ContainerPort } from "@/lib/api"
import { Loader2, ArrowLeft, CheckCircle, Info } from "lucide-react"

interface AppProfile {
  name: string
  recommended_port: number
  healthcheck_path: string | null
  preserve_host_header: boolean
  post_setup_reminder: string | null
  image_patterns: string[]
}

export default function ExposeService() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [settings, setSettings] = useState<AllSettings | null>(null)

  // App profile state
  const [detectedProfile, setDetectedProfile] = useState<string | null>(null)
  const [profileData, setProfileData] = useState<AppProfile | null>(null)

  // Pre-filled from Discover page
  const containerId = searchParams.get("container_id") || ""
  const containerName = searchParams.get("container_name") || ""
  const containerImage = searchParams.get("image") || ""
  const portsJson = searchParams.get("ports") || "[]"
  let availablePorts: ContainerPort[] = []
  try {
    availablePorts = JSON.parse(portsJson)
  } catch {
    // Malformed URL param — fall back to empty
  }

  // Form state
  const [name, setName] = useState(containerName)
  const [hostnamePrefix, setHostnamePrefix] = useState(
    containerName.replace(/[^a-z0-9-]/gi, "-").toLowerCase()
  )
  const [port, setPort] = useState(
    availablePorts.length > 0 ? availablePorts[0].container_port : "80"
  )
  const [scheme, setScheme] = useState("http")
  const [healthcheckPath, setHealthcheckPath] = useState("")
  const [preserveHost, setPreserveHost] = useState(true)
  const [customSnippet, setCustomSnippet] = useState("")
  const [enabled, setEnabled] = useState(true)
  const [appProfile, setAppProfile] = useState<string | null>(null)

  useEffect(() => {
    api.get<AllSettings>("/settings").then(setSettings)

    // Auto-detect app profile from image name
    if (containerImage) {
      api
        .get<{ detected_profile: string | null; profile: AppProfile | null }>(
          `/profiles/detect?image=${encodeURIComponent(containerImage)}`
        )
        .then((res) => {
          if (res.detected_profile && res.profile) {
            setDetectedProfile(res.detected_profile)
            setProfileData(res.profile)
            setAppProfile(res.detected_profile)
            // Apply profile defaults
            setPort(String(res.profile.recommended_port))
            if (res.profile.healthcheck_path) setHealthcheckPath(res.profile.healthcheck_path)
            setPreserveHost(res.profile.preserve_host_header)
          }
        })
        .catch(() => {})
    }
  }, [])

  const baseDomain = settings?.general.base_domain || "example.com"
  const fullHostname = `${hostnamePrefix}.${baseDomain}`

  const handleSubmit = async () => {
    setSaving(true)
    setError(null)
    try {
      const svc = await api.post<ServiceItem>("/services", {
        name,
        upstream_container_id: containerId,
        upstream_container_name: containerName,
        upstream_scheme: scheme,
        upstream_port: Number(port),
        healthcheck_path: healthcheckPath || null,
        hostname: fullHostname,
        base_domain: baseDomain,
        enabled,
        preserve_host_header: preserveHost,
        custom_caddy_snippet: customSnippet || null,
        app_profile: appProfile,
      })
      // Redirect straight to the service detail page
      navigate(`/services/${svc.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(false)
    }
  }

  // --- Wizard form ---
  return (
    <div>
      <button
        onClick={() => navigate(-1)}
        className="mb-4 inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700"
      >
        <ArrowLeft className="h-4 w-4" /> Back
      </button>

      <h1 className="text-2xl font-bold">Expose Service</h1>
      <p className="mt-1 text-sm text-zinc-500">
        Create an edge container for <strong>{containerName || "a Docker container"}</strong>.
      </p>

      <div className="mt-6 max-w-lg space-y-5">
        {/* Profile detected banner */}
        {detectedProfile && profileData && (
          <div className="flex items-start gap-2 rounded-md bg-blue-50 px-4 py-3 text-sm text-blue-800">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Detected <strong>{profileData.name}</strong> profile. Defaults have been applied
              (port {profileData.recommended_port}
              {profileData.healthcheck_path ? `, healthcheck ${profileData.healthcheck_path}` : ""}).
            </span>
          </div>
        )}

        {/* Service name */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Service Name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </label>

        {/* Hostname */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Hostname Prefix</span>
          <input
            type="text"
            value={hostnamePrefix}
            onChange={(e) => setHostnamePrefix(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
            className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
          <p className="mt-1 text-xs text-zinc-400">
            Full URL: <span className="font-medium text-zinc-600">https://{fullHostname}</span>
          </p>
        </label>

        {/* Port */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Upstream Port</span>
          {availablePorts.length > 0 ? (
            <select
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            >
              {availablePorts.map((p) => (
                <option key={`${p.container_port}/${p.protocol}`} value={p.container_port}>
                  {p.container_port}/{p.protocol}
                  {p.host_port ? ` (host: ${p.host_port})` : ""}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="number"
              value={port}
              onChange={(e) => setPort(e.target.value)}
              min={1}
              max={65535}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          )}
        </label>

        {/* Scheme */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Upstream Scheme</span>
          <select
            value={scheme}
            onChange={(e) => setScheme(e.target.value)}
            className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          >
            <option value="http">HTTP</option>
            <option value="https">HTTPS</option>
          </select>
        </label>

        {/* Healthcheck path */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Healthcheck Path (optional)</span>
          <input
            type="text"
            value={healthcheckPath}
            onChange={(e) => setHealthcheckPath(e.target.value)}
            placeholder="/health"
            className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </label>

        {/* Toggles */}
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={preserveHost}
              onChange={(e) => setPreserveHost(e.target.checked)}
              className="rounded border-zinc-300"
            />
            <span className="text-zinc-700">Preserve Host Header</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="rounded border-zinc-300"
            />
            <span className="text-zinc-700">Enable immediately</span>
          </label>
        </div>

        {/* Custom Caddy snippet */}
        <label className="block">
          <span className="text-sm font-medium text-zinc-700">Custom Caddy Snippet (optional)</span>
          <textarea
            value={customSnippet}
            onChange={(e) => setCustomSnippet(e.target.value)}
            rows={3}
            placeholder="Additional Caddy directives..."
            className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
          />
        </label>

        {/* Preview */}
        <div className="rounded-md border border-zinc-200 bg-zinc-50 p-4">
          <h3 className="text-sm font-medium text-zinc-700">Review</h3>
          <dl className="mt-2 space-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-zinc-500">Edge container:</dt>
              <dd className="font-medium text-zinc-700">edge_{hostnamePrefix.replace(/[^a-z0-9]+/g, "-")}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-zinc-500">DNS record:</dt>
              <dd className="font-medium text-zinc-700">{fullHostname}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-zinc-500">Upstream:</dt>
              <dd className="font-medium text-zinc-700">{scheme}://{containerName}:{port}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-zinc-500">Network:</dt>
              <dd className="font-medium text-zinc-700">edge_net_{hostnamePrefix.replace(/[^a-z0-9]+/g, "-")}</dd>
            </div>
          </dl>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
        )}

        {/* Submit */}
        <button
          onClick={handleSubmit}
          disabled={saving || !name || !hostnamePrefix || !port}
          className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
        >
          {saving ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Creating...</>
          ) : (
            <><CheckCircle className="h-4 w-4" /> Create Service</>
          )}
        </button>
      </div>
    </div>
  )
}
