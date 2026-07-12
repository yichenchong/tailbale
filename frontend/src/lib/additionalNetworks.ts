import { type EdgeNetworkAttachment } from "@/lib/api"

export function formatAdditionalNetworks(value: EdgeNetworkAttachment[] | null | undefined): string {
  if (!value || value.length === 0) return "—"
  return value.map((item) => `${item.name}: ${item.aliases.join(", ")}`).join("; ")
}
