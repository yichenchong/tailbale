import { useCallback, useEffect, useRef, useState } from "react"
import { api, type ServiceItem } from "@/lib/api"
import { useResource } from "@/lib/useResource"
import { isServiceName, isUpstreamPort } from "@/lib/validation"
import { type ServiceEditState } from "@/lib/serviceTypes"


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
  // seed is not suppressed by a stale-open form. Derived during render (not an
  // effect) so the closed form is visible on the very first paint of the new
  // id, per React's "adjusting state when a prop changes" pattern.
  const [syncedId, setSyncedId] = useState(id)
  if (id !== syncedId) {
    setSyncedId(id)
    setEditing(false)
  }

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
