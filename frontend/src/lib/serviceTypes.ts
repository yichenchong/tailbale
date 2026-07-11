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
  normalizedName: string
  nameValid: boolean
  portValid: boolean
  reset: () => void
}

export type ServiceLifecycleActionKey =
  | "enable"
  | "disable"
  | "reload"
  | "restart"
  | "recreate"
  | "reconcile"
