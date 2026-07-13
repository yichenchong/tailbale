import { type EdgeNetworkAttachment } from "@/lib/api"

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
  additionalNetworks: EdgeNetworkAttachment[]
  setAdditionalNetworks: (v: EdgeNetworkAttachment[]) => void
  normalizedName: string
  nameValid: boolean
  portValid: boolean
  additionalNetworksValid: boolean
  reset: () => void
}

export type ServiceLifecycleActionKey =
  | "enable"
  | "disable"
  | "reload"
  | "restart"
  | "recreate"
  | "reconcile"
