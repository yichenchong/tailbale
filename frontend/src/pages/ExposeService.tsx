import { Loader2, ArrowLeft, CheckCircle, Info } from "lucide-react"
import { useExposeForm } from "./useExposeForm"
import { AdditionalNetworksEditor } from "@/components/service/AdditionalNetworksEditor"
import { formatAdditionalNetworks } from "@/lib/additionalNetworks"

export default function ExposeService() {
  const {
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
    additionalNetworks,
    setAdditionalNetworks,
    normalizedHostnamePrefix,
    hostnamePrefixValid,
    fullHostname,
    edgeSlug,
    canSubmit,
    handleSubmit,
    goBack,
  } = useExposeForm()

  // --- Wizard form ---
  return (
    <div>
      <button
        onClick={goBack}
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

        <AdditionalNetworksEditor value={additionalNetworks} onChange={setAdditionalNetworks} />

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
            <div className="flex gap-2">
              <dt className="text-zinc-500">Additional networks:</dt>
              <dd className="font-medium text-zinc-700">{formatAdditionalNetworks(additionalNetworks)}</dd>
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
