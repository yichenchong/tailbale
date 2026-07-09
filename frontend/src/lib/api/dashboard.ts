import { get } from "./core"

export interface DashboardSummary {
  services: {
    total: number
    healthy: number
    warning: number
    error: number
  }
  expiring_certs: {
    service_id: string
    service_name: string
    hostname: string
    expires_at: string | null
  }[]
  recent_errors: {
    id: string
    service_id: string | null
    kind: string
    message: string
    created_at: string | null
  }[]
  recent_events: {
    id: string
    service_id: string | null
    kind: string
    level: string
    message: string
    created_at: string | null
  }[]
}

export const dashboardApi = {
  summary: () => get<DashboardSummary>("/dashboard/summary"),
}
