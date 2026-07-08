import { useState, useEffect, useMemo, useRef, type FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { api, type AllSettings, type ContainerPort, type AppProfile } from "@/lib/api"
import { Loader2, ArrowLeft, CheckCircle, Info } from "lucide-react"
import {
  slugify,
  hostnamePrefix as defaultHostnamePrefix,
  isUpstreamPort,
  isServiceName,
  SERVICE_NAME_REQUIRED_MESSAGE,
  SERVICE_NAME_LENGTH_MESSAGE,
  UPSTREAM_PORT_MESSAGE,
} from "@/lib/validation"
import { errorMessage } from "@/lib/utils"

function parsePortsParam(portsJson: string): ContainerPort[] {
  try {
    const parsed = JSON.parse(portsJson)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export default function ExposeService() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [saving, setSaving] = useState(false)
  // In-flight guard for the create POST. A ref (set synchronously) — not the
  // `saving` state — because two submit events can fire within a single React
  // batch (a synchronous double-Enter / programmatic double requestSubmit())
  // before the `saving=true` re-render commits; both would then close over the
  // stale `saving=false` and slip past a state check, POSTing twice. The ref
  // flips immediately, so the second handler sees it. `saving` still drives the
  // button/label UI.
  const submittingRef = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [settings, setSettings] = useState<AllSettings | null>(null)
  const [settingsError, setSettingsError] = useState<string | null>(null)

  // App profile state
  const [detectedProfile, setDetectedProfile] = useState<string | null>(null)
  const [profileData, setProfileData] = useState<AppProfile | null>(null)

  // Pre-filled from Discover page
  const containerId = searchParams.get("container_id") || ""
  const containerName = searchParams.get("container_name") || ""
  const containerImage = searchParams.get("image") || ""
  const portsJson = searchParams.get("ports") || "[]"
  const availablePorts = useMemo(() => parsePortsParam(portsJson), [portsJson])

  // Form state
  const [name, setName] = useState(containerName)
  const [hostnamePrefix, setHostnamePrefix] = useState(
    defaultHostnamePrefix(containerName)
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
    let cancelled = false
    api.settings
      .all()
      .then((data) => {
        if (cancelled) return
        setSettings(data)
        setSettingsError(null)
      })
      .catch((e) => {
        if (cancelled) return
        setSettingsError(errorMessage(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    setName(containerName)
    setHostnamePrefix(defaultHostnamePrefix(containerName))
    setPort(availablePorts.length > 0 ? availablePorts[0].container_port : "80")
    setScheme("http")
    setHealthcheckPath("")
    setPreserveHost(true)
    setCustomSnippet("")
    setEnabled(true)
  }, [containerId, containerName, containerImage, availablePorts])

  useEffect(() => {
    let cancelled = false
    setDetectedProfile(null)
    setProfileData(null)
    setAppProfile(null)

    // Auto-detect app profile from image name
    if (containerImage) {
      api.profiles
        .detect(containerImage)
        .then((res) => {
          if (cancelled) return
          if (res.detected_profile && res.profile) {
            setDetectedProfile(res.detected_profile)
            setProfileData(res.profile)
            setAppProfile(res.detected_profile)
            // Apply profile defaults. Only override the port when the
            // recommended port is actually exposed by the container; otherwise
            // the <select> can't represent it and submission would 422.
            const rec = res.profile.recommended_port
            const recExposed =
              availablePorts.length === 0 ||
              availablePorts.some((p) => String(p.container_port) === String(rec))
            if (recExposed) setPort(String(rec))
            if (res.profile.healthcheck_path) setHealthcheckPath(res.profile.healthcheck_path)
            setPreserveHost(res.profile.preserve_host_header)
          }
        })
        .catch(() => {})
    }
    return () => {
      cancelled = true
    }
  }, [containerId, containerImage, availablePorts])

  const baseDomain = settings?.general.base_domain || "example.com"
  const normalizedName = name.trim()
  const normalizedHostnamePrefix = hostnamePrefix.trim()
  const parsedPort = Number(port)
  const fullHostname = `${normalizedHostnamePrefix}.${baseDomain}`
  // Backend `_unique_slug` caps the base slug at 50 chars before any `-{n}`
  // collision suffix (`_MAX_BASE_SLUG_LEN`); mirror it so the preview matches the
  // server for long names too (strip a trailing dash from the cut, fall back).
  const edgeSlug = slugify(normalizedName).slice(0, 50).replace(/-+$/, "") || "service"
  const hostnamePrefixValid = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(normalizedHostnamePrefix)
  // Port range (1..65535) and name length (<=128) mirror the backend
  // ServiceCreate constraints; both live in lib/validation so ServiceDetail's
  // edit form and this wizard share one copy (see the helpers' doc-comments).
  const portValid = isUpstreamPort(port)
  const nameValid = isServiceName(name)
  const hasSelectedContainer = Boolean(containerId && containerName)
  const canSubmit = Boolean(settings) && hasSelectedContainer && nameValid && hostnamePrefixValid && portValid && !saving

  const handleSubmit = async (e?: FormEvent<HTMLFormElement>) => {
    e?.preventDefault()
    // Re-entrancy guard, checked before any await. The disabled submit button
    // can't stop a second submit fired in the same React batch: it runs before
    // the `saving=true` re-render commits, so the button isn't disabled yet and a
    // `saving` check would still read false. The ref (set synchronously below) is
    // already true by then, so the duplicate bails before POSTing.
    if (submittingRef.current) return
    if (!settings) {
      setError(settingsError || "Settings are still loading")
      return
    }
    if (!hasSelectedContainer) {
      setError("Choose a discovered container before creating a service")
      return
    }
    if (!normalizedName) {
      setError(SERVICE_NAME_REQUIRED_MESSAGE)
      return
    }
    if (!nameValid) {
      setError(SERVICE_NAME_LENGTH_MESSAGE)
      return
    }
    if (!hostnamePrefixValid) {
      setError("Hostname prefix must start and end with a lowercase letter or number, and may contain hyphens")
      return
    }
    if (!portValid) {
      setError(UPSTREAM_PORT_MESSAGE)
      return
    }
    // Mark in-flight before the first await so a same-batch re-submit is blocked.
    submittingRef.current = true
    setSaving(true)
    setError(null)
    try {
      const svc = await api.services.create({
        name: normalizedName,
        upstream_container_id: containerId,
        upstream_container_name: containerName,
        upstream_scheme: scheme,
        upstream_port: parsedPort,
        healthcheck_path: healthcheckPath.trim() || null,
        hostname: fullHostname,
        enabled,
        preserve_host_header: preserveHost,
        custom_caddy_snippet: customSnippet || null,
        app_profile: appProfile,
      })
      // Redirect straight to the service detail page
      navigate(`/services/${encodeURIComponent(svc.id)}`)
    } catch (e) {
      setError(errorMessage(e))
      submittingRef.current = false
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

      {settingsError && (
        <div role="alert" className="mt-4 max-w-lg rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{settingsError}</div>
      )}

      <form className="mt-6 max-w-lg space-y-5" onSubmit={handleSubmit}>
        {/* Profile detected banner */}
        {detectedProfile && profileData && (
          <div className="flex items-start gap-2 rounded-md bg-blue-50 px-4 py-3 text-sm text-blue-800">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <div className="space-y-1">
              <span>
                Detected <strong>{profileData.name}</strong> profile. Defaults have been applied
                (port {profileData.recommended_port}
                {profileData.healthcheck_path ? `, healthcheck ${profileData.healthcheck_path}` : ""}).
              </span>
              {profileData.post_setup_reminder && (
                <p className="text-blue-900">
                  <strong>After creating:</strong> {profileData.post_setup_reminder}
                </p>
              )}
            </div>
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
          {!normalizedHostnamePrefix ? (
            <p className="mt-1 text-xs text-red-600">Hostname prefix is required.</p>
          ) : !hostnamePrefixValid ? (
            <p className="mt-1 text-xs text-red-600">
              Must start and end with a lowercase letter or number; hyphens allowed in between.
            </p>
          ) : null}
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
              <dd className="font-medium text-zinc-700">edge_{edgeSlug}</dd>
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
              <dd className="font-medium text-zinc-700">edge_net_{edgeSlug}</dd>
            </div>
          </dl>
        </div>

        {/* Error */}
        {error && (
          <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={!canSubmit}
          aria-busy={saving}
          className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
        >
          {saving ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Creating...</>
          ) : (
            <><CheckCircle className="h-4 w-4" /> Create Service</>
          )}
        </button>
      </form>
    </div>
  )
}
