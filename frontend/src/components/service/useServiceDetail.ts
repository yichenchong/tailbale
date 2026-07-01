import { useCallback, useEffect, useRef, useState } from "react"
import { api, type ServiceItem } from "@/lib/api"
import { useResource } from "@/lib/useResource"
import { isServiceName, isUpstreamPort } from "@/lib/validation"

/** Editable configuration fields + their live validity, owned by the hook. */
export interface ServiceEditState {
  editing: boolean
  setEditing: (v: boolean) => void
  name: string
  setName: (v: string) => void
  port: string
  setPort: (v: string) => void
  scheme: string
  setScheme: (v: string) => void
  healthcheck: string
  setHealthcheck: (v: string) => void
  preserveHost: boolean
  setPreserveHost: (v: boolean) => void
  snippet: string
  setSnippet: (v: string) => void
  /** Trimmed name (mirrors the backend strip) for messages + request bodies. */
  normalizedName: string
  /** Name length within backend bounds (see {@link isServiceName}). */
  nameValid: boolean
  /** Port in the backend 1..65535 range (see {@link isUpstreamPort}). */
  portValid: boolean
  /** Re-seed every field from the current service (Cancel), ignoring the guard. */
  reset: () => void
}

export interface UseServiceDetailResult {
  service: ServiceItem | null
  loading: boolean
  error: string | null
  refresh: (opts?: { background?: boolean }) => Promise<void>
  applyServiceUpdate: (svc: ServiceItem) => void
  setError: (value: string | null) => void
  edit: ServiceEditState
}

/**
 * Fetch/seed machine for the ServiceDetail page. Wraps `useResource` for the
 * detail GET (race-guarded, id-keyed) and owns the edit-form field state,
 * re-seeding it from a winning response — but never clobbering a form the user
 * has open (the `editingRef` guard). Navigation to a new id closes the form so
 * the next load reseeds cleanly.
 */
export function useServiceDetail(id: string | undefined): UseServiceDetailResult {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState("")
  const [port, setPort] = useState("")
  const [scheme, setScheme] = useState("http")
  const [healthcheck, setHealthcheck] = useState("")
  const [preserveHost, setPreserveHost] = useState(true)
  const [snippet, setSnippet] = useState("")
  const editingRef = useRef(false)

  const seedFrom = useCallback((svc: ServiceItem) => {
    setName(svc.name)
    setPort(String(svc.upstream_port))
    setScheme(svc.upstream_scheme)
    setHealthcheck(svc.healthcheck_path || "")
    setPreserveHost(svc.preserve_host_header)
    setSnippet(svc.custom_caddy_snippet || "")
  }, [])

  const fetcher = useCallback(() => api.services.get(id ?? ""), [id])
  // Re-seed the edit fields from a winning detail response, but never clobber an
  // open edit form (the mount/navigation load runs with editing closed).
  const seedEditForm = useCallback((svc: ServiceItem) => {
    if (editingRef.current) return
    seedFrom(svc)
  }, [seedFrom])

  const {
    data: service,
    loading,
    error,
    refresh,
    setData: applyServiceUpdate,
    setError,
  } = useResource(fetcher, {
    onData: seedEditForm,
    mapError: (e) => (e instanceof Error && e.message ? e.message : "Service not found"),
  })

  // Cancel: force-reseed from the current service regardless of the guard.
  const reset = useCallback(() => {
    if (service) seedFrom(service)
  }, [service, seedFrom])

  // Close the edit form when navigating to another service so the next load's
  // seed is not suppressed by a stale-open form.
  useEffect(() => {
    setEditing(false)
  }, [id])

  useEffect(() => {
    editingRef.current = editing
  }, [editing])

  const normalizedName = name.trim()
  const nameValid = isServiceName(name)
  const portValid = isUpstreamPort(port)

  return {
    service,
    loading,
    error,
    refresh,
    applyServiceUpdate,
    setError,
    edit: {
      editing,
      setEditing,
      name,
      setName,
      port,
      setPort,
      scheme,
      setScheme,
      healthcheck,
      setHealthcheck,
      preserveHost,
      setPreserveHost,
      snippet,
      setSnippet,
      normalizedName,
      nameValid,
      portValid,
      reset,
    },
  }
}
