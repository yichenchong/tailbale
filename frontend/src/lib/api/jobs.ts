import { del, get, post } from "./core"

export interface JobDetails {
  record_id: string
  hostname: string
  zone_id: string
  value: string | null
  service_name: string
}

export interface OrphanJob {
  id: string
  service_id: string | null
  kind: string
  status: string
  progress: number
  message: string | null
  details: JobDetails | null
  created_at: string | null
  updated_at: string | null
}

export interface JobsResponse {
  jobs: OrphanJob[]
  total: number
}

export interface JobsQuery {
  kind?: string
  limit: number
  offset: number
}

export interface JobActionResult {
  success: boolean
  message: string
}

export const jobsApi = {
  list: (params: JobsQuery) => {
    const qs = new URLSearchParams()
    if (params.kind) qs.set("kind", params.kind)
    qs.set("limit", String(params.limit))
    qs.set("offset", String(params.offset))
    return get<JobsResponse>(`/jobs?${qs.toString()}`)
  },
  retry: (id: string) => post<JobActionResult>(`/jobs/${encodeURIComponent(id)}/retry`),
  dismiss: (id: string) => del<void>(`/jobs/${encodeURIComponent(id)}`),
}
