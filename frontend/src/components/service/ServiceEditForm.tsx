import { useId, useState } from "react"
import { Loader2, Save } from "lucide-react"
import { AdditionalNetworksEditor } from "./AdditionalNetworksEditor"
import { formatAdditionalNetworks } from "@/lib/additionalNetworks"
import { api, type ServiceItem, type ServiceUpdateRequest } from "@/lib/api"
import {
  SERVICE_NAME_REQUIRED_MESSAGE,
  SERVICE_NAME_LENGTH_MESSAGE,
  UPSTREAM_PORT_MESSAGE,
  normalizeAdditionalNetworks,
} from "@/lib/validation"
import { Row } from "./Row"
import { type ServiceEditState } from "@/lib/serviceTypes"
import { errorMessage } from "@/lib/utils"

/**
 * Configuration card: the read-only detail list plus the inline edit form. Owns
 * the in-flight `saving` state and the PUT; validity comes from the shared
 * validators via {@link ServiceEditState}. On a successful save it installs the
 * authoritative response through `applyServiceUpdate` (race-guarded) and closes
 * the form; validation/HTTP errors surface through the page-level `setError`.
 */
export function ServiceEditForm({
  service,
  id,
  edit,
  applyServiceUpdate,
  setError,
}: {
  service: ServiceItem
  id: string | undefined
  edit: ServiceEditState
  applyServiceUpdate: (svc: ServiceItem) => void
  setError: (value: string | null) => void
}) {
  const [saving, setSaving] = useState(false)
  const nameErrorId = useId()
  const nameError = edit.normalizedName !== "" && !edit.nameValid
  const canSave = edit.nameValid && edit.portValid && edit.additionalNetworksValid && !saving

  const handleSave = async () => {
    if (!edit.normalizedName) {
      setError(SERVICE_NAME_REQUIRED_MESSAGE)
      return
    }
    if (!edit.nameValid) {
      setError(SERVICE_NAME_LENGTH_MESSAGE)
      return
    }
    if (!edit.portValid) {
      setError(UPSTREAM_PORT_MESSAGE)
      return
    }
    if (!edit.additionalNetworksValid) {
      setError("Additional edge networks require a valid Docker network name and at least one valid hostname alias")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const normalizedNetworks = normalizeAdditionalNetworks(edit.additionalNetworks)
      const body: ServiceUpdateRequest = {
        name: edit.normalizedName,
        upstream_port: Number(edit.port),
        upstream_scheme: edit.scheme,
        healthcheck_path: edit.healthcheck.trim() || null,
        preserve_host_header: edit.preserveHost,
        custom_caddy_snippet: edit.snippet || null,
      }
      // Preserve the null-vs-[] distinction the backend relies on. A non-empty
      // list is sent as-is (managed convergence). An empty editor is only sent
      // when it *clears* a previously-configured list (send [] to disconnect the
      // now-unmanaged networks); when the service had none, the field is omitted
      // so an unrelated edit never opts a service into edge-network management.
      if (normalizedNetworks.length > 0) {
        body.additional_networks = normalizedNetworks
      } else if ((service.additional_networks?.length ?? 0) > 0) {
        body.additional_networks = []
      }
      const svc = await api.services.update(id ?? "", body)
      applyServiceUpdate(svc)
      edit.setEditing(false)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-md border border-zinc-200 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-zinc-700">Configuration</h2>
        {!edit.editing && (
          <button onClick={() => edit.setEditing(true)} className="text-xs font-medium text-zinc-500 hover:text-zinc-700">
            Edit
          </button>
        )}
      </div>

      {edit.editing ? (
        <div className="mt-3 space-y-3">
          <label className="block">
            <span className="text-xs font-medium text-zinc-600">Name</span>
            <input type="text" value={edit.name} onChange={(e) => edit.setName(e.target.value)}
              aria-invalid={nameError ? true : undefined}
              aria-describedby={nameError ? nameErrorId : undefined}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
          </label>
          {nameError && (
            <p id={nameErrorId} className="mt-1 text-xs text-red-600">{SERVICE_NAME_LENGTH_MESSAGE}.</p>
          )}
          <label className="block">
            <span className="text-xs font-medium text-zinc-600">Upstream Port</span>
            <input type="number" value={edit.port} onChange={(e) => edit.setPort(e.target.value)}
              min={1} max={65535} step={1}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
          </label>
          <label className="block">
            <span className="text-xs font-medium text-zinc-600">Scheme</span>
            <select value={edit.scheme} onChange={(e) => edit.setScheme(e.target.value)}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500">
              <option value="http">HTTP</option>
              <option value="https">HTTPS</option>
            </select>
          </label>
          <label className="block">
            <span className="text-xs font-medium text-zinc-600">Healthcheck Path</span>
            <input type="text" value={edit.healthcheck} onChange={(e) => edit.setHealthcheck(e.target.value)}
              placeholder="/health"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={edit.preserveHost} onChange={(e) => edit.setPreserveHost(e.target.checked)} className="rounded border-zinc-300" />
            <span className="text-zinc-600">Preserve Host Header</span>
          </label>
          <label className="block">
            <span className="text-xs font-medium text-zinc-600">Custom Caddy Snippet</span>
            <textarea value={edit.snippet} onChange={(e) => edit.setSnippet(e.target.value)} rows={2}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500" />
          </label>
          <AdditionalNetworksEditor value={edit.additionalNetworks} onChange={edit.setAdditionalNetworks} />
          <div className="flex gap-2">
            <button onClick={handleSave} disabled={!canSave}
              className="inline-flex items-center gap-1 rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50">
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
              Save
            </button>
            <button onClick={() => {
              edit.reset()
              edit.setEditing(false)
            }} className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <dl className="mt-3 space-y-2 text-sm">
          <Row label="Upstream" value={`${service.upstream_scheme}://${service.upstream_container_name}:${service.upstream_port}`} />
          <Row label="Hostname" value={service.hostname} />
          <Row label="Base Domain" value={service.base_domain} />
          <Row label="Healthcheck" value={service.healthcheck_path || "—"} />
          <Row label="Preserve Host" value={service.preserve_host_header ? "Yes" : "No"} />
          <Row label="App Profile" value={service.app_profile || "—"} />
          <Row label="Additional Networks" value={formatAdditionalNetworks(service.additional_networks)} />
        </dl>
      )}
    </div>
  )
}
