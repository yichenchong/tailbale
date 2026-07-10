import { useCallback, useState, useEffect, useMemo, useRef, type FormEvent } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { api, type AllSettings, type ContainerPort, type AppProfile } from "@/lib/api"
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
import { useResource } from "@/lib/useResource"
import { useLatestRequest } from "@/lib/useLatestRequest"

function parsePortsParam(portsJson: string): ContainerPort[] {
  try {
    const parsed = JSON.parse(portsJson)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/**
 * Data-fetch (settings + profile detect) + form-state + create-mutation logic
 * for the Expose Service wizard (AR10). Extracted from ExposeService.tsx so the
 * page is primarily presentational; behavior is unchanged, including the
 * synchronous double-submit ref guard, slug-preview parity, and every
 * client-side validation gate.
 */
export function useExposeForm() {
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
  const settingsFetcher = useCallback(() => api.settings.all(), [])
  const { data: settings, error: settingsError } = useResource<AllSettings>(settingsFetcher)

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
  const profileRequest = useLatestRequest()


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
    const token = profileRequest.next()
    setDetectedProfile(null)
    setProfileData(null)
    setAppProfile(null)

    // Auto-detect app profile from image name
    if (containerImage) {
      api.profiles
        .detect(containerImage)
        .then((res) => {
          if (!profileRequest.isCurrent(token)) return
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
      profileRequest.invalidate()
    }
  }, [containerId, containerName, containerImage, availablePorts, profileRequest])

  const baseDomain = settings?.general.base_domain || "example.com"
  const normalizedName = name.trim()
  const normalizedHostnamePrefix = hostnamePrefix.trim()
  const parsedPort = Number(port)
  const fullHostname = `${normalizedHostnamePrefix}.${baseDomain}`
  // Backend `unique_slug` caps the base slug at 50 chars before any `-{n}`
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

  const goBack = () => navigate(-1)

  return {
    saving,
    error,
    settingsError,
    detectedProfile,
    profileData,
    containerName,
    availablePorts,
    name,
    setName,
    hostnamePrefix,
    setHostnamePrefix,
    port,
    setPort,
    scheme,
    setScheme,
    healthcheckPath,
    setHealthcheckPath,
    preserveHost,
    setPreserveHost,
    customSnippet,
    setCustomSnippet,
    enabled,
    setEnabled,
    normalizedHostnamePrefix,
    hostnamePrefixValid,
    fullHostname,
    edgeSlug,
    canSubmit,
    handleSubmit,
    goBack,
  }
}
