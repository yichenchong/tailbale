import { Plus, Trash2 } from "lucide-react"
import { type EdgeNetworkAttachment } from "@/lib/api"
import { isDockerNetworkName, isHostnameAlias } from "@/lib/validation"


export function AdditionalNetworksEditor({
  value,
  onChange,
}: {
  value: EdgeNetworkAttachment[]
  onChange: (value: EdgeNetworkAttachment[]) => void
}) {
  const update = (index: number, patch: Partial<EdgeNetworkAttachment>) => {
    onChange(value.map((item, i) => (i === index ? { ...item, ...patch } : item)))
  }
  const remove = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-2 rounded-md border border-zinc-200 p-3">
      <div>
        <span className="text-sm font-medium text-zinc-700">Additional Edge Networks</span>
        <p className="mt-1 text-xs text-zinc-500">
          Attach the edge container to existing Docker networks and register hostname aliases there, e.g. <code>cloud.example.com</code> on <code>opencloud_opencloud-net</code>.
        </p>
      </div>

      {value.map((item, index) => {
        const normalizedName = item.name.trim()
        const nameInvalid = normalizedName !== "" && !isDockerNetworkName(normalizedName)
        // Ignore in-progress empty tokens (a trailing separator the user just
        // typed) so a red error doesn't flash between aliases; empties are
        // dropped on submit by normalizeAdditionalNetworks.
        const aliasesInvalid = item.aliases.some((alias) => alias !== "" && !isHostnameAlias(alias))
        const aliasesEmpty = normalizedName !== "" && item.aliases.every((alias) => alias === "")
        return (
          <div key={index} className="space-y-1 rounded-md bg-zinc-50 p-2">
            <div className="flex gap-2">
              <label className="min-w-0 flex-1">
                <span className="text-xs font-medium text-zinc-600">Docker network</span>
                <input
                  type="text"
                  value={item.name}
                  onChange={(e) => update(index, { name: e.target.value })}
                  placeholder="opencloud_opencloud-net"
                  aria-label={`Additional Docker network ${index + 1}`}
                  className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
                />
              </label>
              <button
                type="button"
                onClick={() => remove(index)}
                aria-label={`Remove additional network ${index + 1}`}
                className="mt-5 rounded-md border border-zinc-300 p-1.5 text-zinc-500 hover:bg-zinc-100"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
            {nameInvalid && (
              <p className="text-xs text-red-600">Network names may contain letters, numbers, underscores, periods, and hyphens.</p>
            )}
            <label className="block">
              <span className="text-xs font-medium text-zinc-600">Aliases</span>
              <input
                type="text"
                value={item.aliases.join(", ")}
                onChange={(e) => update(index, {
                  // Keep empty tokens so a trailing separator survives the
                  // controlled re-render; filtering here would strip the space/
                  // comma the user just typed and make a second alias untypable.
                  aliases: e.target.value
                    .split(/[\s,]+/)
                    .map((alias) => alias.trim().toLowerCase()),
                })}
                placeholder="cloud.example.com"
                aria-label={`Aliases for additional network ${index + 1}`}
                className="mt-1 block w-full rounded-md border border-zinc-300 px-2.5 py-1.5 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            {(aliasesInvalid || aliasesEmpty) && (
              <p className="text-xs text-red-600">Add at least one lowercase hostname alias; aliases must be valid DNS names.</p>
            )}
          </div>
        )
      })}

      <button
        type="button"
        onClick={() => onChange([...value, { name: "", aliases: [] }])}
        className="inline-flex items-center gap-1 rounded-md border border-zinc-300 px-2.5 py-1.5 text-xs font-medium text-zinc-600 hover:bg-zinc-50"
      >
        <Plus className="h-3.5 w-3.5" /> Add network
      </button>
    </div>
  )
}
